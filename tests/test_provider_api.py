"""Parsing and rate-limit-safety tests for the usage endpoint provider.

The live payload these fixtures mirror was captured on 2026-09-07 from
GET https://api.anthropic.com/api/oauth/usage.
"""

from datetime import datetime, timedelta, timezone

import pytest
import responses

from clausage_bar import auth, config
from clausage_bar.provider_api import ApiProvider, parse

# Trimmed copy of a real response, including the internal codename keys that
# must be ignored rather than crash us.
LIVE_BODY = {
    "five_hour": {"utilization": 45.0, "resets_at": "2026-09-07T13:30:00.798800+00:00",
                  "limit_dollars": None, "used_dollars": None,
                  "remaining_dollars": None, "locked_reason": None},
    "seven_day": {"utilization": 7.0, "resets_at": "2026-09-14T05:00:00.798820+00:00",
                  "limit_dollars": None, "used_dollars": None,
                  "remaining_dollars": None, "locked_reason": None},
    "seven_day_opus": None,
    "seven_day_sonnet": None,
    "seven_day_cowork": None,
    "nimbus_quill": {"utilization": 0.0, "resets_at": None},
    "tangelo": None,
    "extra_usage": {"is_enabled": True, "monthly_limit": 5000, "used_credits": 3300.0,
                    "utilization": 66.0, "currency": "USD", "decimal_places": 2,
                    "spend_limit_reached": False},
    "limits": [
        {"kind": "session", "group": "session", "percent": 45, "severity": "normal",
         "resets_at": "2026-09-07T13:30:00.798800+00:00", "scope": None,
         "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 7, "severity": "normal",
         "resets_at": "2026-09-14T05:00:00.798820+00:00", "scope": None,
         "is_active": False},
    ],
    "spend": {
        "used": {"amount_minor": 3400, "currency": "USD", "exponent": 2},
        "limit": {"amount_minor": 5000, "currency": "USD", "exponent": 2},
        "percent": 68, "severity": "normal", "enabled": True,
    },
    "member_dashboard_available": False,
}


class TestParse:
    def test_reads_both_primary_windows(self):
        windows, _ = parse(LIVE_BODY)
        assert windows["five_hour"].utilization == 45.0
        assert windows["seven_day"].utilization == 7.0

    def test_prefers_the_limits_array(self):
        """limits[] carries severity and is_active, which the top level lacks."""
        windows, _ = parse(LIVE_BODY)
        assert windows["five_hour"].raw_key_path == "limits[session]"
        assert windows["five_hour"].severity == "normal"
        assert windows["five_hour"].is_active is True

    def test_reset_times_are_utc_aware(self):
        windows, _ = parse(LIVE_BODY)
        resets = windows["five_hour"].resets_at
        assert resets.tzinfo is not None
        assert resets == datetime(2026, 9, 7, 13, 30, 0, 798800, tzinfo=timezone.utc)

    def test_null_windows_stay_none_not_zero(self):
        windows, _ = parse(LIVE_BODY)
        assert windows["seven_day_opus"] is None

    def test_internal_codename_keys_are_ignored(self):
        windows, _ = parse(LIVE_BODY)
        assert "nimbus_quill" not in windows
        assert "tangelo" not in windows

    def test_spend_prefers_the_spend_object(self):
        _, spend = parse(LIVE_BODY)
        assert spend.percent == 68.0
        assert spend.used_amount == 34.00
        assert spend.limit_amount == 50.0
        assert spend.currency == "USD"

    def test_spend_falls_back_to_extra_usage(self):
        body = dict(LIVE_BODY)
        body.pop("spend")
        _, spend = parse(body)
        assert spend.percent == 66.0
        assert spend.used_amount == 33.00

    def test_falls_back_to_top_level_when_limits_absent(self):
        body = dict(LIVE_BODY)
        body.pop("limits")
        windows, _ = parse(body)
        assert windows["five_hour"].utilization == 45.0
        assert windows["five_hour"].raw_key_path == "five_hour"

    def test_empty_and_garbage_payloads_do_not_crash(self):
        for body in ({}, {"limits": "nope"}, {"limits": [None, 3]}):
            windows, spend = parse(body)
            assert windows.get("five_hour") is None
            assert spend is None


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(auth, "cli_version", lambda: "2.1.263")
    monkeypatch.setattr(auth, "account_info", lambda: {})
    monkeypatch.setattr(auth, "load", lambda: auth.Credentials(
        access_token="test-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        refresh_token_expires_at=datetime.now(timezone.utc) + timedelta(days=30)))
    return ApiProvider()


class TestRateLimitSafety:
    """A missing User-Agent lands in an aggressively throttled bucket."""

    @responses.activate
    def test_user_agent_is_always_sent(self, provider):
        responses.add(responses.GET, config.USAGE_URL, json=LIVE_BODY, status=200)
        assert provider.poll() is not None
        sent = responses.calls[0].request.headers
        assert sent["User-Agent"] == "claude-code/2.1.263"
        assert sent["anthropic-beta"] == config.ANTHROPIC_BETA
        assert sent["Authorization"] == "Bearer test-token"

    @responses.activate
    def test_backoff_escalates_then_resets(self, provider):
        responses.add(responses.GET, config.USAGE_URL, status=429)
        assert provider.interval_s() in (config.POLL_S, config.IDLE_POLL_S)

        observed = []
        for _ in range(5):
            provider.poll()
            observed.append(provider.interval_s())
        assert observed == [180, 360, 720, 1800, 1800]

        responses.reset()
        responses.add(responses.GET, config.USAGE_URL, json=LIVE_BODY, status=200)
        assert provider.poll() is not None
        assert provider.interval_s() in (config.POLL_S, config.IDLE_POLL_S)

    @responses.activate
    def test_retry_after_header_is_honored(self, provider):
        responses.add(responses.GET, config.USAGE_URL, status=429,
                      headers={"retry-after": "2400"})
        provider.poll()
        assert provider.interval_s() == 180        # ladder value...
        assert provider._next_allowed > 0          # ...but scheduled far out

    @responses.activate
    def test_refresh_now_never_overrides_a_backoff(self, provider):
        responses.add(responses.GET, config.USAGE_URL, status=429)
        provider.poll()
        before = provider._next_allowed
        provider.force_due()
        assert provider._next_allowed == before
        assert not provider.due()


class TestErrorHandling:
    @responses.activate
    def test_server_error_returns_none_without_raising(self, provider):
        responses.add(responses.GET, config.USAGE_URL, status=503)
        assert provider.poll() is None
        assert provider.last_error == "HTTP 503"

    @responses.activate
    def test_non_json_body(self, provider):
        responses.add(responses.GET, config.USAGE_URL, body="<html>", status=200)
        assert provider.poll() is None
        assert provider.last_error == "bad JSON"

    @responses.activate
    def test_payload_without_windows_is_rejected(self, provider):
        responses.add(responses.GET, config.USAGE_URL,
                      json={"member_dashboard_available": False}, status=200)
        assert provider.poll() is None
        assert provider.last_error == "no windows in response"

    @responses.activate
    def test_persistent_401_reports_actionable_reason(self, provider, monkeypatch):
        monkeypatch.setattr(provider.refresher, "trigger", lambda **kw: False)
        responses.add(responses.GET, config.USAGE_URL, status=401)
        assert provider.poll() is None
        assert "token expired" in provider.unavailable_reason

    def test_signed_out_is_detected_without_a_request(self, provider, monkeypatch):
        monkeypatch.setattr(auth, "load", lambda: auth.Credentials(
            access_token="t", expires_at=None,
            refresh_token_expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
        assert provider.poll() is None
        assert "claude auth login" in provider.unavailable_reason

    def test_missing_credentials_is_reported(self, provider, monkeypatch):
        monkeypatch.setattr(auth, "load", lambda: None)
        assert provider.poll() is None
        assert provider.unavailable_reason == "no credentials"


class TestTokenSafety:
    def test_redacted_view_omits_both_tokens(self):
        creds = auth.Credentials(
            access_token="sk-secret", expires_at=None,
            refresh_token_expires_at=None)
        blob = repr(creds.redacted())
        assert "sk-secret" not in blob
        assert "accessToken" not in blob
        assert creds.redacted()["has_access_token"] is True

    def test_snapshot_never_carries_the_token(self, provider):
        with responses.RequestsMock() as mock:
            mock.add(responses.GET, config.USAGE_URL, json=LIVE_BODY, status=200)
            snapshot = provider.poll()
        assert "test-token" not in repr(snapshot.to_json())


class TestRefreshLadder:
    """Rotation is proved by the token changing, not by an exit code.

    `claude auth status --json` was observed to exit 0 while leaving a live
    token untouched, so trusting its exit code would report a refresh that
    never happened.
    """

    @pytest.fixture
    def trigger(self, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "claude_exe", lambda: "claude.exe")
        return auth.RefreshTrigger(cooldown_s=0)

    def _tokens(self, monkeypatch, sequence):
        """Make auth.load() walk a scripted list of tokens."""
        box = {"i": 0}

        def fake_load():
            i = min(box["i"], len(sequence) - 1)
            return auth.Credentials(access_token=sequence[i], expires_at=None,
                                    refresh_token_expires_at=None)
        monkeypatch.setattr(auth, "load", fake_load)
        return box

    def test_stops_at_the_cheap_step_when_it_rotates(self, trigger, monkeypatch):
        box = self._tokens(monkeypatch, ["old", "new"])
        ran = []

        def fake_run(exe, tail):
            ran.append(tail[0])
            box["i"] = 1                       # this command rotated the token
            return True
        monkeypatch.setattr(trigger, "_run", fake_run)

        assert trigger.trigger(require_rotation=True) is True
        assert ran == ["auth"]                 # never escalated to update
        assert trigger.last_effective_step == "auth-status"

    def test_escalates_when_the_cheap_step_does_nothing(self, trigger, monkeypatch):
        box = self._tokens(monkeypatch, ["old", "new"])
        ran = []

        def fake_run(exe, tail):
            ran.append(tail[0])
            if tail[0] == "update":
                box["i"] = 1                   # only `update` rotates it
            return True
        monkeypatch.setattr(trigger, "_run", fake_run)

        assert trigger.trigger(require_rotation=True) is True
        assert ran == ["auth", "update"]
        assert trigger.last_effective_step == "update"

    def test_reports_failure_when_nothing_rotates(self, trigger, monkeypatch):
        self._tokens(monkeypatch, ["old"])
        monkeypatch.setattr(trigger, "_run", lambda exe, tail: True)
        assert trigger.trigger(require_rotation=True) is False
        assert trigger.last_effective_step is None

    def test_no_rotation_required_accepts_a_clean_exit(self, trigger, monkeypatch):
        self._tokens(monkeypatch, ["old"])
        ran = []
        monkeypatch.setattr(trigger, "_run",
                            lambda exe, tail: ran.append(tail[0]) or True)
        assert trigger.trigger(require_rotation=False) is True
        assert ran == ["auth"]

    def test_explicit_override_pins_one_command(self, trigger, monkeypatch):
        monkeypatch.setattr(config, "REFRESH_COMMAND", "update")
        self._tokens(monkeypatch, ["old"])
        ran = []
        monkeypatch.setattr(trigger, "_run",
                            lambda exe, tail: ran.append(tail[0]) or True)
        trigger.trigger(require_rotation=True)
        assert ran == ["update"]

    def test_disabled_by_config(self, trigger, monkeypatch):
        monkeypatch.setattr(config, "REFRESH_COMMAND", "none")
        assert trigger.available() is False
        assert trigger.trigger() is False

    def test_cooldown_blocks_a_second_attempt(self, monkeypatch):
        monkeypatch.setattr(auth, "claude_exe", lambda: "claude.exe")
        t = auth.RefreshTrigger(cooldown_s=300)
        self._tokens(monkeypatch, ["old"])
        monkeypatch.setattr(t, "_run", lambda exe, tail: True)
        t.trigger()
        assert t.available() is False
        assert t.trigger() is False

    def test_fingerprint_never_exposes_the_token(self):
        creds = auth.Credentials(access_token="sk-super-secret", expires_at=None,
                                 refresh_token_expires_at=None)
        fp = auth.token_fingerprint(creds)
        assert fp and len(fp) == 12
        assert "secret" not in fp
        assert auth.token_fingerprint(None) is None
