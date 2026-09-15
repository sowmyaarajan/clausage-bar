"""Start-with-Windows via a Startup-folder shortcut.

A .lnk is discoverable and removable in Explorer and is what Task Manager's
Startup tab manages. Created through WScript.Shell so we need no pywin32.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import config
from .logging_setup import get

log = get("autostart")

CREATE_NO_WINDOW = 0x08000000
LINK_NAME = "clausage_bar.lnk"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData/Roaming"
    return base / "Microsoft/Windows/Start Menu/Programs/Startup"


def link_path() -> Path:
    return startup_dir() / LINK_NAME


def pythonw() -> Path:
    """The console-less interpreter beside the current one.

    sys.executable is the venv's own pythonw.exe even though the venv launcher
    re-execs the base interpreter underneath (that shows up as two pythonw
    processes, parent and child -- benign). Taking the base one instead would
    be wrong: the dependencies live in the venv, not in the base install.
    """
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else Path(sys.executable)


def can_run(exe: Path) -> bool:
    """Whether `exe` can actually import what the tray needs.

    Skipped entirely in the standalone build: the probe works by running
    `exe -c "import pystray"`, and a frozen executable has no interpreter to
    accept `-c`. It would reject the flag, the probe would read that as a
    missing dependency, and autostart would refuse to enable itself -- on the
    one build where the dependencies are guaranteed present, because they are
    inside the executable.

    Worth the ~200ms. An autostart entry pointing at an interpreter without
    pystray fails invisibly -- no window, no error, just no tray icon at every
    logon from now on. That is exactly the failure this check exists to turn
    into a log line at the moment the user asks for autostart, rather than a
    mystery weeks later. The base interpreter beside a venv is the realistic
    way to get here.
    """
    if config.FROZEN:
        return True
    try:
        done = subprocess.run([str(exe), "-c", "import pystray, PIL.Image"],
                              capture_output=True, text=True, timeout=60,
                              creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("could not probe %s: %s", exe, exc)
        return False
    if done.returncode != 0:
        log.error("%s cannot import the tray dependencies: %s",
                  exe, (done.stderr or "").strip().splitlines()[-1:])
        return False
    return True


def is_enabled() -> bool:
    return link_path().exists()


def launcher() -> Path:
    """run_tray.pyw puts src/ on sys.path, so the shortcut needs no PYTHONPATH."""
    return Path(__file__).resolve().parents[2] / "run_tray.pyw"


def enable() -> bool:
    target = link_path()
    if config.FROZEN:
        # The executable *is* the launcher. No interpreter, no script path,
        # and no arguments -- pointing the shortcut at a `.pyw` that does not
        # exist inside the bundle is how this silently produced a shortcut
        # that did nothing at every logon.
        exe = Path(sys.executable)
        arguments = ""
        workdir = exe.parent
    else:
        exe = pythonw()
        entry = launcher()
        if not entry.exists():
            log.error("launcher missing: %s", entry)
            return False
        if not can_run(exe):
            return False    # a shortcut that fails silently is worse than none
        arguments = '"{0}"'.format(entry)
        workdir = entry.parent
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath='{exe}';"
        "$s.Arguments='{args}';"
        "$s.WorkingDirectory='{cwd}';"
        "$s.Description='Claude usage tray monitor';"
        "$s.WindowStyle=7;"
        "$s.Save()"
    ).format(link=target, exe=exe, args=arguments, cwd=workdir)
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", script],
                       capture_output=True, text=True, timeout=60, check=True,
                       creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("could not create startup shortcut: %s", exc)
        return False
    log.info("startup shortcut created at %s", target)
    return True


def disable() -> bool:
    try:
        link_path().unlink(missing_ok=True)
        log.info("startup shortcut removed")
        return True
    except OSError as exc:
        log.error("could not remove startup shortcut: %s", exc)
        return False


def toggle() -> bool:
    return disable() if is_enabled() else enable()
