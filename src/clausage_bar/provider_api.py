"""The api.anthropic.com/api/oauth/usage provider.

This endpoint is undocumented and unsupported. Two rules keep it working:
  * Always send ``User-Agent: claude-code/<version>``. Without it the request
    lands in an aggressively rate limited bucket and 429s persistently.
  * Never poll faster than 180s, and back off hard on 429.

Parsing prefers the ``limits`` array, which carries the server's own ``kind``,
``percent``, ``severity`` and ``is_active`` for each window, and falls back to
the top-level ``five_hour`` / ``seven_day`` objects.
"""

from __future__ import annotations

import ctypes
import time
from datetime import datetime, timezone
from typing import Any

import requests

from . import auth, config
from .logging_setup import get
from .model import Snapshot, Spend, WindowUsage

log = get("api")

# limits[].kind -> our window name
_KIND_MAP = {
    "session": "five_hour",
    "weekly_all": "seven_day",
    "weekly_opus": "seven_day_opus",
    "weekly_sonnet": "seven_day_sonnet",
}

# Fallback: top-level objects we understand. Anything else in the payload
# (rotating internal codenames like "nimbus_quill") is ignored by design.
_TOP_LEVEL = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")


def _idle_seconds() -> float:
    """Seconds since the last user input, via GetLastInputInfo."""

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (tick - info.dwTime) / 1000.0)
    except (AttributeError, OSError):
        return 0.0


def _claude_running() -> bool:
    try:
        return any(config.SESSIONS_DIR.glob("*.json"))
    except OSError:
        return False


def parse(body: Any) -> tuple[dict[str, WindowUsage | None], Spend | None]:
    """Normalize a usage response. Unknown/null windows become None."""
    windows: dict[str, WindowUsage | None] = {}

    limits = body.get("limits") if isinstance(body, dict) else None
    if isinstance(limits, list):
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            name = _KIND_MAP.get(entry.get("kind"))
            if not name:
                continue
            parsed = WindowUsage.from_payload(
                entry, key_path="limits[{0}]".format(entry.get("kind")))
            if parsed:
                windows[name] = parsed

    for name in _TOP_LEVEL:
        if windows.get(name) is not None:
            continue
        windows[name] = WindowUsage.from_payload((body or {}).get(name), key_path=name)

    spend = Spend.from_payload((body or {}).get("spend"), (body or {}).get("extra_usage"))
    return windows, spend


class ApiProvider:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.refresher = auth.RefreshTrigger()
        self.cc_version = auth.cli_version()
        self.account = auth.account_info()
        self._backoff_index = -1          # -1 == no backoff active
        self._next_allowed = 0.0
        self.last_error: str | None = None
        self.unavailable_reason: str | None = None
        log.info("api provider ready (User-Agent claude-code/%s)", self.cc_version)

    # ---------------------------------------------------------------- cadence

    def interval_s(self) -> int:
        """How long to wait before the next poll."""
        if self._backoff_index >= 0:
            return config.BACKOFF_LADDER_S[
                min(self._backoff_index, len(config.BACKOFF_LADDER_S) - 1)]
        if _idle_seconds() > config.IDLE_AFTER_S and not _claude_running():
            return config.IDLE_POLL_S
        return config.POLL_S

    def due(self) -> bool:
        return time.monotonic() >= self._next_allowed

    def _schedule(self, seconds: float) -> None:
        self._next_allowed = time.monotonic() + seconds

    def force_due(self) -> None:
        """Menu "Refresh now": bypass the interval, but never an active backoff."""
        if self._backoff_index < 0:
            self._next_allowed = 0.0

    def _on_success(self) -> None:
        if self._backoff_index >= 0:
            log.info("429 backoff cleared after a successful request")
        self._backoff_index = -1
        self.last_error = None
        self.unavailable_reason = None
        self._schedule(self.interval_s())

    def _on_rate_limit(self, retry_after: str | None) -> None:
        first = self._backoff_index < 0
        self._backoff_index = min(self._backoff_index + 1,
                                  len(config.BACKOFF_LADDER_S) - 1)
        wait = config.BACKOFF_LADDER_S[self._backoff_index]
        if retry_after:
            try:
                wait = max(wait, int(float(retry_after)))
            except ValueError:
                pass
        if first:
            log.warning("HTTP 429 from usage endpoint -- backing off %ss. A persistent "
                        "429 usually means the User-Agent is missing or wrong.", wait)
        else:
            log.warning("HTTP 429 again -- backing off %ss", wait)
        self.last_error = "rate limited"
        self._schedule(wait)

    # ---------------------------------------------------------------- request

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": "Bearer {0}".format(token),
            "anthropic-beta": config.ANTHROPIC_BETA,
            "User-Agent": "claude-code/{0}".format(self.cc_version),
            "Content-Type": "application/json",
        }

    def _get(self, token: str) -> requests.Response:
        return self.session.get(config.USAGE_URL, headers=self._headers(token),
                                timeout=config.HTTP_TIMEOUT_S)

    def poll(self) -> Snapshot | None:
        """One attempt. Returns a Snapshot, or None with the reason recorded."""
        # Cheap unless ~/.claude.json actually changed, and this is what makes
        # a team or account switch show up without restarting the tray.
        self.account = auth.account_info()

        creds = auth.load()
        if creds is None:
            self.unavailable_reason = "no credentials"
            self._schedule(config.POLL_S)
            return None
        if creds.signed_out():
            self.unavailable_reason = "signed out - run: claude auth login"
            log.error("refresh token expired; user must run: claude auth login")
            self._schedule(config.POLL_S)
            return None

        # Proactive refresh: ask Claude Code to rotate its own token.
        if creds.expired() and self.refresher.available():
            if self.refresher.trigger(require_rotation=True):
                creds = auth.load() or creds

        try:
            resp = self._get(creds.access_token)
        except requests.RequestException as exc:
            self.last_error = type(exc).__name__
            log.warning("usage request failed: %s", exc)
            self._schedule(self.interval_s())
            return None

        if resp.status_code == 401:
            log.info("401 from usage endpoint; requesting a token refresh")
            if self.refresher.trigger(require_rotation=True):
                refreshed = auth.load()
                if refreshed is not None:
                    try:
                        resp = self._get(refreshed.access_token)
                    except requests.RequestException as exc:
                        self.last_error = type(exc).__name__
                        self._schedule(self.interval_s())
                        return None
            if resp.status_code == 401:
                self.unavailable_reason = "token expired - open Claude Code once"
                log.error("still 401 after refresh attempt")
                self._schedule(config.POLL_S)
                return None

        if resp.status_code == 429:
            self._on_rate_limit(resp.headers.get("retry-after"))
            return None

        if resp.status_code != 200:
            self.last_error = "HTTP {0}".format(resp.status_code)
            log.warning("usage endpoint returned %s: %s",
                        resp.status_code, resp.text[:200])
            self._schedule(self.interval_s())
            return None

        try:
            body = resp.json()
        except ValueError:
            self.last_error = "bad JSON"
            log.warning("usage endpoint returned a non-JSON body")
            self._schedule(self.interval_s())
            return None

        windows, spend = parse(body)
        if windows.get("five_hour") is None and windows.get("seven_day") is None:
            self.last_error = "no windows in response"
            log.warning("usage response had neither window; keys=%s",
                        sorted(body) if isinstance(body, dict) else type(body))
            self._schedule(self.interval_s())
            return None

        self._on_success()
        return Snapshot(
            captured_at=datetime.now(timezone.utc),
            source="api",
            windows=windows,
            spend=spend,
            account=self.account,
            sources=["api"],
        )
