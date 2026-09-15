"""Windows toast notifications, with two fallbacks.

winotify is preferred: pure Python, no compiled dependencies, and it works
under pythonw.exe. If it is missing we emit the same toast XML through
PowerShell ourselves; if that fails too we fall back to the tray balloon,
which is always available.
"""

from __future__ import annotations

import subprocess

from . import config
from .logging_setup import get

log = get("notify")

CREATE_NO_WINDOW = 0x08000000

_PS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
<toast><visual><binding template="ToastGeneric"><text>__TITLE__</text><text>__BODY__</text></binding></visual></toast>
"@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("__APPID__").Show($toast)
"""


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


class Notifier:
    def __init__(self, enabled: bool = True, tray=None) -> None:
        self.enabled = enabled
        self.tray = tray
        self._winotify = None
        if enabled:
            try:
                from winotify import Notification  # noqa: F401
                self._winotify = Notification
            except Exception as exc:                # pragma: no cover
                log.info("winotify unavailable (%s); will use PowerShell", exc)

    def notify(self, title: str, body: str) -> bool:
        if not self.enabled:
            log.info("toast suppressed (--no-toast): %s | %s", title, body)
            return False
        for attempt in (self._via_winotify, self._via_powershell, self._via_balloon):
            try:
                if attempt(title, body):
                    log.info("toast: %s | %s", title, body)
                    return True
            except Exception as exc:
                log.warning("%s failed: %s", attempt.__name__, exc)
        log.error("all toast methods failed")
        return False

    # ---------------------------------------------------------------- backends

    def _via_winotify(self, title: str, body: str) -> bool:
        if self._winotify is None:
            return False
        toast = self._winotify(app_id=config.APP_ID, title=title, msg=body)
        toast.add_actions(label="Open usage page", launch=config.USAGE_PAGE_URL)
        toast.show()
        return True

    def _via_powershell(self, title: str, body: str) -> bool:
        script = (_PS_TOAST
                  .replace("__TITLE__", _xml_escape(title))
                  .replace("__BODY__", _xml_escape(body))
                  .replace("__APPID__", config.APP_ID))
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW)
        return proc.returncode == 0

    def _via_balloon(self, title: str, body: str) -> bool:
        if self.tray is None:
            return False
        self.tray.notify(body, title)
        return True
