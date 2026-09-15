"""Account and team metadata.

Every test here monkeypatches ``config.CLAUDE_JSON`` to a tmp_path. That is
not incidental: CLAUDE_JSON is derived from HOME and is *not* affected by
CLAUDE_CONFIG_DIR, so a test that tries to sandbox itself through the
environment writes to the developer's real ~/.claude.json. It happened.
"""

import json

import pytest

from clausage_bar import auth, config

ACCOUNT = {
    "userRateLimitTier": "default_tier",
    "seatTier": "example_tier",
    "hasExtraUsageEnabled": True,
    "organizationName": "Acme Corp",
    "organizationType": "claude_team",
}


@pytest.fixture
def claude_json(tmp_path, monkeypatch):
    """A throwaway ~/.claude.json, and a guarantee we never read the real one."""
    path = tmp_path / ".claude.json"
    monkeypatch.setattr(config, "CLAUDE_JSON", path)
    monkeypatch.setattr(auth, "_ACCOUNT_CACHE", None)

    def write(account=None, **extra):
        payload = {"oauthAccount": dict(account or ACCOUNT)}
        payload.update(extra)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    write.path = path
    return write


class TestSandbox:
    def test_the_fixture_points_away_from_home(self, claude_json):
        """The guard rail for the mistake that motivated this file."""
        claude_json()
        assert config.CLAUDE_JSON == claude_json.path
        assert "AppData" in str(config.CLAUDE_JSON) or \
            "Temp" in str(config.CLAUDE_JSON) or \
            "pytest" in str(config.CLAUDE_JSON)


class TestReading:
    def test_it_captures_the_team_name(self, claude_json):
        claude_json()
        assert auth.account_info()["org_name"] == "Acme Corp"

    def test_it_captures_the_tier_and_credits_flag(self, claude_json):
        claude_json()
        info = auth.account_info()
        assert info["seat_tier"] == "example_tier"
        assert info["has_extra_usage"] is True

    def test_a_missing_file_is_not_an_error(self, claude_json, monkeypatch):
        monkeypatch.setattr(config, "CLAUDE_JSON",
                            claude_json.path.parent / "nope.json")
        monkeypatch.setattr(auth, "_ACCOUNT_CACHE", None)
        assert auth.account_info() == {}

    def test_a_file_without_an_account_block_is_not_an_error(self, claude_json):
        claude_json.path.write_text(json.dumps({"projects": {}}),
                                    encoding="utf-8")
        assert auth.account_info() == {}

    def test_a_torn_file_is_not_an_error(self, claude_json):
        """Claude Code rewrites this file; a reader can catch it mid-write."""
        claude_json.path.write_text('{"oauthAccount": {"organi',
                                    encoding="utf-8")
        assert auth.account_info() == {}

    def test_no_token_is_ever_read_from_here(self, claude_json):
        """This file is for context only. Credentials live elsewhere."""
        claude_json(account=dict(ACCOUNT, accessToken="secret-should-be-ignored"))
        info = auth.account_info()
        assert "secret-should-be-ignored" not in json.dumps(info)
        assert not any("token" in k.lower() for k in info)


class TestTeamChanges:
    """A team change must appear without restarting the tray.

    It used to be read once in ApiProvider.__init__ and reused for every
    snapshot, so the panel kept showing the old team indefinitely.
    """

    def test_an_unchanged_file_is_not_reparsed(self, claude_json):
        claude_json()
        first = auth.account_info()
        assert auth.account_info() is first

    def test_a_changed_team_is_picked_up(self, claude_json):
        claude_json()
        assert auth.account_info()["org_name"] == "Acme Corp"

        claude_json(account=dict(ACCOUNT, organizationName="Acme Labs"))
        # The stat has to differ for the cache to miss; bump it explicitly
        # rather than sleeping, so the test cannot be flaky on a coarse clock.
        import os
        stat = claude_json.path.stat()
        os.utime(claude_json.path, ns=(stat.st_atime_ns,
                                       stat.st_mtime_ns + 1_000_000))
        assert auth.account_info()["org_name"] == "Acme Labs"

    def test_a_changed_account_is_picked_up(self, claude_json):
        claude_json()
        auth.account_info()
        claude_json(account=dict(ACCOUNT, seatTier="enterprise"))
        import os
        stat = claude_json.path.stat()
        os.utime(claude_json.path, ns=(stat.st_atime_ns,
                                       stat.st_mtime_ns + 1_000_000))
        assert auth.account_info()["seat_tier"] == "enterprise"

    def test_force_bypasses_the_cache(self, claude_json):
        claude_json()
        auth.account_info()
        claude_json(account=dict(ACCOUNT, organizationName="Elsewhere"))
        assert auth.account_info(force=True)["org_name"] == "Elsewhere"

    def test_the_provider_refreshes_it_every_poll(self):
        """Otherwise the cache above would never be consulted again."""
        import inspect
        from clausage_bar import provider_api
        source = inspect.getsource(provider_api.ApiProvider.poll)
        assert "auth.account_info()" in source
