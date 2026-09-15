"""Guards on the distribution scripts.

These are text assertions over PowerShell rather than behavioural tests, which
is a real limitation: they cannot prove the packager works, only that its
exclusion list still names the things that must never ship. That is worth
having anyway, because the failure being guarded against is silent. A dropped
line in ``$dropFiles`` produces a zip that installs perfectly and happens to
carry someone's OAuth token, and no test that only checks "does it install"
would notice.

The one thing here that is genuinely verified is that every path the scripts
reference actually exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install"


def read(name: str) -> str:
    path = INSTALL / name if not name.endswith(".cmd") else ROOT / name
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8", errors="replace")


class TestPackager:
    """The exclusion list is the whole security surface of the packager."""

    @pytest.mark.parametrize("must_exclude", [
        ".credentials.json",   # the OAuth token. Never.
        ".claude.json*",       # account identity and project history
        "snapshot.json",       # would show the recipient someone else's usage
        "notif_state.json",
        "state.json",
        "*.log",
    ])
    def test_personal_files_are_excluded(self, must_exclude):
        assert must_exclude in read("package.ps1")

    @pytest.mark.parametrize("must_exclude", [
        ".venv",            # hardcodes absolute interpreter paths; not portable
        "__pycache__",
        ".pytest_cache",
        "clausage",         # the runtime state directory
    ])
    def test_unportable_dirs_are_excluded(self, must_exclude):
        assert f"'{must_exclude}'" in read("package.ps1")

    def test_it_refuses_to_ship_a_token_shaped_string(self):
        """A belt-and-braces grep, for a token pasted into a comment.

        The exclusion list only catches files with known names. This catches
        the case the list cannot: a real token living inside a source file.
        """
        body = read("package.ps1")
        assert "sk-ant-" in body
        assert "refusing to package" in body

    def test_a_leak_check_failure_deletes_the_staging_dir(self):
        """It must not leave a staged copy containing the thing it rejected."""
        body = read("package.ps1")
        # Every `throw "refusing to package...` is preceded by a cleanup.
        for match in re.finditer(r"throw \"refusing to package", body):
            before = body[max(0, match.start() - 200):match.start()]
            assert "Remove-Item $stage -Recurse -Force" in before


class TestPreflight:
    """The two hard blockers must stay hard, and the rest must stay soft."""

    def test_missing_credentials_is_a_blocker(self):
        body = read("preflight.ps1")
        # `Bad` accumulates into $blockers and forces exit 1; `Warn` does not.
        creds = body[body.index("$creds = Join-Path"):body.index("# ---", body.index("$creds = Join-Path"))]
        assert "Bad" in creds

    def test_missing_node_is_only_a_warning(self):
        """Node costs the fallback source, not the app. It must not block."""
        body = read("preflight.ps1")
        node = body[body.index("$node = Get-Command node"):]
        node = node[:node.index("# ---")]
        assert "Warn" in node
        assert "Bad" not in node

    def test_it_sends_a_user_agent_on_the_reachability_probe(self):
        """A request without it is what trips the endpoint's rate limiter."""
        body = read("preflight.ps1")
        assert "'User-Agent' = $ua" in body
        assert "claude-code/" in body

    def test_a_4xx_counts_as_reachable(self):
        """Unauthenticated, 401 is correct and 429 is the documented reflex.

        Treating either as a failure told the user their network was broken
        when it was fine -- which is how this check first behaved.
        """
        body = read("preflight.ps1")
        assert "$code -ge 400 -and $code -lt 500" in body

    def test_it_never_prints_the_token(self):
        """It reads .credentials.json to check shape, so this matters."""
        body = read("preflight.ps1")
        assert "[bool]$json.claudeAiOauth.accessToken" in body
        # The value itself must never reach a Write-Host / Ok / Warn / Bad.
        for line in body.splitlines():
            if re.search(r"\b(Ok|Warn|Bad|Write-Host)\b", line):
                assert "accessToken" not in line, line


class TestInstaller:
    def test_it_runs_preflight_before_touching_anything(self):
        body = read("install.ps1")
        assert "preflight.ps1" in body
        assert body.index("preflight.ps1") < body.index("Creating the virtual environment")

    def test_a_blocker_changes_nothing(self):
        assert "Nothing on this machine has been changed." in read("install.ps1")

    def test_it_falls_back_when_the_py_launcher_is_absent(self):
        """A Store Python has no `py`; dying on that names the wrong problem."""
        body = read("install.ps1")
        assert "Get-Command py -ErrorAction SilentlyContinue" in body

    def test_the_statusline_smoke_test_is_guarded_on_the_file(self):
        """statusline.js is a user's own script; recipients often have none.

        Unguarded this ran `node` against a missing path and aborted the
        install over an optional fallback that had just been skipped.
        """
        body = read("install.ps1")
        probe = body.index("must still render with no rate_limits")
        assert "if (Test-Path $statusline) {" in body[probe:probe + 700]

    def test_missing_node_skips_the_statusline_step(self):
        assert "Get-Command node -ErrorAction SilentlyContinue" in read("install.ps1")


class TestEntryPoint:
    def test_install_cmd_bypasses_execution_policy(self):
        """A .ps1 from a network share or zip is blocked by RemoteSigned."""
        body = read("Install.cmd")
        assert "-ExecutionPolicy Bypass" in body
        assert "install\\install.ps1" in body

    def test_it_forwards_switches(self):
        """So `Install.cmd -NoStatusline` reaches install.ps1."""
        assert "%*" in read("Install.cmd")

    def test_it_holds_the_window_open(self):
        """Double-clicked, the window would close with the result inside it."""
        assert "pause" in read("Install.cmd")

    def test_every_referenced_script_exists(self):
        """The one thing here that is actually verified rather than asserted."""
        for name in ("install.ps1", "uninstall.ps1", "preflight.ps1",
                     "package.ps1", "pin_to_taskbar.ps1"):
            assert (INSTALL / name).exists(), name
        for name in ("Install.cmd", "run_tray.pyw", "run_clausage.cmd",
                     "requirements.txt"):
            assert (ROOT / name).exists(), name


class TestNoIdentityShipped:
    """The packaged app must not carry the identity of whoever built it.

    Every value here is read from the *installing* user's own
    ``~/.claude.json`` at runtime, so a literal in the source is always either
    a leftover or a bug. These caught three real ones: the team name in two
    code comments and a README example, an account tier in four fixtures, and
    a real credit balance baked into six test files.
    """

    SHIPPED = ("src", "tests", "tools", "collector", "install")

    # LICENSE names the copyright holder on purpose; that is the one place a
    # real name belongs. Everything else at the repo root is scanned.
    NAME_EXEMPT = frozenset({"LICENSE"})

    # Root-level files were originally outside this scan, and that gap shipped
    # a real leak: requirements-frozen-.venv.txt carried a "Frozen from
    # C:\Users\<name>\..." header naming the build machine. The packager
    # rewrote it in its staging copy, so every packaged zip was clean and the
    # source file stayed untouched -- invisible until the repo was published.
    def _shipped_text(self):
        for path in ROOT.iterdir():
            if path.is_file() and path.suffix.lower() in (
                    ".md", ".txt", ".cmd", ".pyw", ".py", ".toml", ".cfg"):
                yield path, path.read_text(encoding="utf-8", errors="replace")
        for name in ("LICENSE", ".gitignore"):
            path = ROOT / name
            if path.is_file():
                yield path, path.read_text(encoding="utf-8", errors="replace")
        for entry in self.SHIPPED:
            path = ROOT / entry
            for child in path.rglob("*"):
                if child.suffix.lower() in (".py", ".js", ".ps1", ".md",
                                            ".txt", ".cmd", ".pyw"):
                    yield child, child.read_text(encoding="utf-8",
                                                 errors="replace")

    # Assembled from fragments rather than written out, because this file is
    # itself in SHIPPED -- spelling the needles literally made every one of
    # these fail on the test that contains them. Splitting keeps this file
    # under the same scrutiny as the rest instead of exempting it, which would
    # have been the easy fix and a hole in the guard.
    @pytest.mark.parametrize("parts", [
        ("Ui", "Path"),         # the builder's employer
        ("Sow", "mya"),         # the builder's name
        ("sk-", "ant-api"),     # an Anthropic key, ever
        ("@ui", "path.com"),    # the builder's work email domain
        # The builder's own account tiers. These are real API enum values, so
        # production code is free to parse them -- but a *fixture* that hard-
        # codes them tells a recipient what seat the builder is on, and any
        # placeholder exercises the underscore-to-title rendering just as well.
        ("default_", "raven"),
        ("team_", "standard"),
    ])
    def test_no_builder_identity(self, parts):
        literal = "".join(parts)
        hits = [str(p.relative_to(ROOT)) for p, body in self._shipped_text()
                if literal.lower() in body.lower()
                and p.name not in self.NAME_EXEMPT]
        assert not hits, f"{literal!r} is shipped in: {hits}"

    def test_no_machine_hostname(self):
        """A Windows default hostname: DESKTOP- plus 7 uppercase alphanumerics.

        Matched by shape and case-sensitively, not as the substring "desktop-".
        That looser version fired on the README's own prose about Claude
        Desktop not being enough to authenticate -- a guard that cries wolf on
        legitimate text gets switched off, which is worse than not having it.
        """
        import re
        pattern = re.compile(r"\bDESKTOP-[A-Z0-9]{7}\b")
        hits = [str(p.relative_to(ROOT)) for p, body in self._shipped_text()
                if pattern.search(body)]
        assert not hits, f"a machine hostname is shipped in: {hits}"

    def test_no_absolute_user_path(self):
        r"""A hardcoded C:\Users\<name> path is both a leak and a bug."""
        import re
        pattern = re.compile(r"[Cc]:[\/]+Users[\/]+(?!<|%|\$)[A-Za-z]")
        hits = [str(p.relative_to(ROOT)) for p, body in self._shipped_text()
                if pattern.search(body)]
        assert not hits, f"absolute user paths in: {hits}"

    def test_the_org_name_is_never_a_literal(self):
        """It must only ever arrive from the account block at runtime.

        The whole point is that a recipient sees *their* team, and the failure
        mode is silent: a literal would show them the builder's team while
        looking perfectly correct.
        """
        source = (ROOT / "src" / "clausage_bar" / "auth.py").read_text(encoding="utf-8")
        assert 'acct.get("organizationName")' in source
