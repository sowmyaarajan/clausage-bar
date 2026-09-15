"""Autostart. The failure mode that matters is the silent one.

A Startup shortcut pointing at an interpreter that cannot import pystray
produces no window and no error -- just no tray icon, at every logon, until
someone thinks to look. So enable() refuses to write one.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from clausage_bar import autostart


class TestPaths:
    def test_link_lives_in_the_startup_folder(self):
        link = autostart.link_path()
        assert link.name == "clausage_bar.lnk"
        assert link.parent.name == "Startup"

    def test_interpreter_is_console_less(self):
        assert autostart.pythonw().name in ("pythonw.exe", "python.exe")

    def test_interpreter_comes_from_the_current_environment(self):
        """Not the base interpreter: the dependencies live in the venv."""
        assert autostart.pythonw().parent == Path(sys.executable).parent

    def test_launcher_exists_and_is_windowless(self):
        entry = autostart.launcher()
        assert entry.exists()
        assert entry.suffix == ".pyw"


class TestCanRun:
    def test_accepts_the_running_interpreter(self):
        assert autostart.can_run(Path(sys.executable))

    def test_rejects_an_interpreter_without_the_dependencies(self, monkeypatch):
        def fake(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 1, "", "ModuleNotFoundError: No module named 'pystray'")
        monkeypatch.setattr(autostart.subprocess, "run", fake)
        assert autostart.can_run(Path("python.exe")) is False

    def test_rejects_a_missing_interpreter(self, monkeypatch):
        def fake(cmd, **kwargs):
            raise OSError("not found")
        monkeypatch.setattr(autostart.subprocess, "run", fake)
        assert autostart.can_run(Path("nope.exe")) is False


class TestEnable:
    def test_refuses_to_write_a_shortcut_that_cannot_work(self, monkeypatch):
        """The whole point: no shortcut beats a silently broken one."""
        calls = []
        monkeypatch.setattr(autostart, "can_run", lambda _exe: False)
        monkeypatch.setattr(autostart.subprocess, "run",
                            lambda *a, **k: calls.append(a))
        assert autostart.enable() is False
        assert calls == []                 # never got as far as PowerShell

    def test_refuses_when_the_launcher_is_missing(self, monkeypatch):
        monkeypatch.setattr(autostart, "launcher",
                            lambda: Path("no_such_launcher.pyw"))
        monkeypatch.setattr(autostart, "can_run",
                            lambda _exe: pytest.fail("probed before checking"))
        assert autostart.enable() is False

    def test_shortcut_targets_the_launcher_not_the_package(self, monkeypatch):
        """run_tray.pyw puts src/ on sys.path, so no PYTHONPATH is needed."""
        seen = {}

        def fake(cmd, **kwargs):
            seen["script"] = cmd[-1]
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(autostart, "can_run", lambda _exe: True)
        monkeypatch.setattr(autostart.subprocess, "run", fake)
        assert autostart.enable() is True
        script = seen["script"]
        assert "run_tray.pyw" in script
        assert "WindowStyle=7" in script       # minimised: nothing flashes


class TestFrozenBuild:
    """The standalone .exe, where three of the assumptions above are false.

    Every one of these fails silently in the frozen build rather than raising,
    which is why they are pinned:

      * `sys.executable` is the app, so it accepts neither `-m` nor `-c`.
      * There is no `pythonw.exe` beside it to prefer.
      * `run_tray.pyw` is not in the bundle, so the shortcut had nothing real
        to point at -- it would have been written, looked fine in Explorer,
        and done nothing at every logon.
    """

    def test_the_dependency_probe_is_skipped(self, monkeypatch):
        """`exe -c "import pystray"` cannot work: there is no interpreter.

        Left in place it would reject the flag, the probe would read that as a
        missing dependency, and autostart would refuse to enable itself on the
        one build where the dependencies are guaranteed to be present.
        """
        monkeypatch.setattr(autostart.config, "FROZEN", True)
        monkeypatch.setattr(
            autostart.subprocess, "run",
            lambda *a, **k: pytest.fail("probed a frozen executable"))
        assert autostart.can_run(Path("anything.exe")) is True

    def test_the_shortcut_targets_the_executable_itself(self, monkeypatch):
        seen = {}

        def fake(cmd, **kwargs):
            seen["script"] = cmd[-1]
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(autostart.config, "FROZEN", True)
        monkeypatch.setattr(autostart.sys, "executable",
                            r"C:\pkg\clausage_bar.exe")
        monkeypatch.setattr(autostart.subprocess, "run", fake)
        assert autostart.enable() is True

        script = seen["script"]
        assert r"TargetPath='C:\pkg\clausage_bar.exe'" in script
        # No arguments: the exe is the launcher. A leftover .pyw path here is
        # precisely the silently-broken shortcut this build had to avoid.
        assert "$s.Arguments='';" in script
        assert ".pyw" not in script
        assert r"WorkingDirectory='C:\pkg'" in script
        assert "WindowStyle=7" in script

    def test_it_does_not_look_for_a_missing_launcher(self, monkeypatch):
        """launcher() points outside the bundle, so it must not be consulted."""
        monkeypatch.setattr(autostart.config, "FROZEN", True)
        monkeypatch.setattr(autostart.sys, "executable", r"C:\pkg\app.exe")
        monkeypatch.setattr(
            autostart, "launcher",
            lambda: pytest.fail("consulted run_tray.pyw in a frozen build"))
        monkeypatch.setattr(
            autostart.subprocess, "run",
            lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
        assert autostart.enable() is True


class TestFrozenSurfaceRouting:
    """Opening the panel from the tray, in the frozen build."""

    def test_the_panel_is_launched_by_flag_not_by_module(self, monkeypatch):
        """`-m clausage_bar.panel` needs an interpreter the build has not got.

        Unfixed, clicking the tray icon did nothing at all: Popen raised, the
        handler logged a warning nobody sees, and the panel never opened.
        """
        from clausage_bar import app as app_module

        seen = {}

        class FakeTray:
            visible = True

        def fake_popen(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["cwd"] = kwargs.get("cwd")
            return None

        monkeypatch.setattr(app_module.config, "FROZEN", True)
        monkeypatch.setattr(app_module.sys, "executable", r"C:\pkg\app.exe")
        monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)

        inst = object.__new__(app_module.TrayApp)
        assert inst._spawn("clausage_bar.panel", "panel") is True
        assert seen["cmd"] == [r"C:\pkg\app.exe", "--panel"]
        # cwd must not be the source tree: it does not exist in the bundle.
        assert seen["cwd"] is None

    def test_the_widget_flag_matches(self, monkeypatch):
        from clausage_bar import app as app_module

        seen = {}
        monkeypatch.setattr(app_module.config, "FROZEN", True)
        monkeypatch.setattr(app_module.sys, "executable", r"C:\pkg\app.exe")
        monkeypatch.setattr(app_module.subprocess, "Popen",
                            lambda cmd, **k: seen.update(cmd=cmd))

        inst = object.__new__(app_module.TrayApp)
        inst._spawn("clausage_bar.widget", "widget")
        assert seen["cmd"] == [r"C:\pkg\app.exe", "--widget"]

    def test_both_flags_are_accepted_by_the_entry_point(self):
        """The flags must exist, or the frozen exe cannot answer as a panel."""
        from clausage_bar.__main__ import main
        import inspect
        source = inspect.getsource(main)
        assert '"--panel"' in source
        assert '"--widget"' in source
