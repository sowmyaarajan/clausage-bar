"""OAuth token access -- strictly read-only.

Rules enforced here:
  * The credentials file is re-read on every poll. Claude Code rewrites it when
    it refreshes; caching the token would guarantee stale-token 401s.
  * The access token is returned only for use in an Authorization header. It is
    never logged, never persisted, never included in a diagnostic dump.
  * We never run the OAuth refresh ourselves. Refresh tokens can be single-use,
    and racing Claude Code's own refresh could sign the user out of the CLI.
    Instead we invoke an official non-interactive CLI command and let Claude
    Code do it, then re-read the file.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import config
from .atomicjson import read_json
from .logging_setup import get

log = get("auth")

CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class Credentials:
    access_token: str
    expires_at: datetime | None
    refresh_token_expires_at: datetime | None
    subscription_type: str | None = None
    rate_limit_tier: str | None = None

    def expired(self, skew_s: int = config.TOKEN_SKEW_S) -> bool:
        if self.expires_at is None:
            return False          # unknown expiry: let the server decide
        return datetime.now(timezone.utc).timestamp() >= (
            self.expires_at.timestamp() - skew_s
        )

    def signed_out(self) -> bool:
        if self.refresh_token_expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.refresh_token_expires_at

    def redacted(self) -> dict[str, Any]:
        """Safe-to-log view. Deliberately omits both tokens."""
        return {
            "has_access_token": bool(self.access_token),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "expired": self.expired(),
            "signed_out": self.signed_out(),
            "subscription_type": self.subscription_type,
            "rate_limit_tier": self.rate_limit_tier,
        }


def _epoch_ms(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def load() -> Credentials | None:
    """Read ~/.claude/.credentials.json. Returns None if unusable."""
    raw = read_json(config.CREDENTIALS_FILE)
    if not isinstance(raw, dict):
        log.warning("credentials file missing or unreadable: %s",
                    config.CREDENTIALS_FILE)
        return None
    oauth = raw.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        log.warning("credentials file has no claudeAiOauth block")
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        log.warning("no access token present")
        return None
    return Credentials(
        access_token=token,
        expires_at=_epoch_ms(oauth.get("expiresAt")),
        refresh_token_expires_at=_epoch_ms(oauth.get("refreshTokenExpiresAt")),
        subscription_type=oauth.get("subscriptionType"),
        rate_limit_tier=oauth.get("rateLimitTier"),
    )


# Cached on the file's (mtime, size) rather than for the process lifetime.
#
# This was read once in ApiProvider.__init__ and reused for every snapshot,
# which meant the team name was only ever as current as the last restart --
# switch organisation and the panel would keep the old one indefinitely. The
# file is ~110KB and grows with project history, so re-parsing it every poll
# would be wasteful; keying on the stat is the cheap middle.
_ACCOUNT_CACHE: tuple[object, dict[str, Any]] | None = None


def _account_stamp() -> object:
    try:
        stat = config.CLAUDE_JSON.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def account_info(force: bool = False) -> dict[str, Any]:
    """Account and team metadata from ~/.claude.json.

    Re-read whenever the file changes, so signing into a different account or
    moving to a different team is picked up on the next poll rather than on
    the next restart.
    """
    global _ACCOUNT_CACHE

    stamp = _account_stamp()
    if (not force and stamp is not None and _ACCOUNT_CACHE is not None
            and _ACCOUNT_CACHE[0] == stamp):
        return _ACCOUNT_CACHE[1]

    info = _read_account()
    if _ACCOUNT_CACHE is not None:
        was = _ACCOUNT_CACHE[1].get("org_name")
        now = info.get("org_name")
        if was != now:
            log.info("team changed: %r -> %r", was, now)
    _ACCOUNT_CACHE = (stamp, info)
    return info


def _read_account() -> dict[str, Any]:
    raw = read_json(config.CLAUDE_JSON, default={})
    acct = raw.get("oauthAccount") if isinstance(raw, dict) else None
    if not isinstance(acct, dict):
        return {}
    return {
        "rate_limit_tier": acct.get("userRateLimitTier"),
        "seat_tier": acct.get("seatTier"),
        "has_extra_usage": acct.get("hasExtraUsageEnabled"),
        # The team this seat belongs to ("Acme Corp"). More useful in a header
        # than the plan tier: on a team seat the limits are the org's.
        "org_name": acct.get("organizationName"),
        "org_type": acct.get("organizationType"),
    }


def claude_exe() -> str | None:
    return shutil.which("claude")


def cli_version() -> str:
    """Claude Code version, for the required User-Agent."""
    exe = claude_exe()
    if exe:
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                                 timeout=15, creationflags=CREATE_NO_WINDOW)
            for token in (out.stdout or "").split():
                if token[:1].isdigit() and "." in token:
                    return token
        except (OSError, subprocess.SubprocessError):
            pass
    return config.FALLBACK_CC_VERSION


def token_fingerprint(creds: "Credentials | None") -> str | None:
    """A short hash, so rotation can be detected without handling the token."""
    if creds is None:
        return None
    return hashlib.sha256(creds.access_token.encode()).hexdigest()[:12]


# Ordered cheapest-first. Each entry is (name, argv-tail).
#   auth status - fast and read-only. Verified to exit 0 and leave a live token
#                 untouched, so its exit code alone proves nothing about
#                 rotation; it may still rotate an expired one.
#   update      - heavier (it can also update the CLI) but community-reported
#                 to reliably refresh. Only reached when the cheap command did
#                 not actually change the token.
REFRESH_LADDER = (
    ("auth-status", ["auth", "status", "--json"]),
    ("update", ["update"]),
)


class RefreshTrigger:
    """Asks Claude Code to rotate its own token, at most once per cooldown.

    We never run the OAuth refresh ourselves: refresh tokens can be single-use,
    and racing Claude Code's own refresh could sign the user out of the CLI.

    Success is measured by the stored token actually changing, not by an exit
    code, because ``claude auth status`` exits 0 whether or not it rotated
    anything. When the cheap command does not rotate an expired token, the
    ladder escalates.
    """

    def __init__(self, cooldown_s: int = config.REFRESH_COOLDOWN_S) -> None:
        self.cooldown_s = cooldown_s
        self._last_attempt = 0.0
        self.last_effective_step: str | None = None

    def available(self) -> bool:
        if config.REFRESH_COMMAND == "none" or claude_exe() is None:
            return False
        return (time.monotonic() - self._last_attempt) >= self.cooldown_s

    def _ladder(self) -> tuple:
        """Honor an explicit override; otherwise walk the whole ladder."""
        mode = config.REFRESH_COMMAND
        chosen = tuple(step for step in REFRESH_LADDER if step[0] == mode)
        return chosen or REFRESH_LADDER

    def _run(self, exe: str, tail: list[str]) -> bool:
        log.info("token refresh attempt: claude %s", " ".join(tail))
        try:
            proc = subprocess.run([exe] + tail, capture_output=True, text=True,
                                  timeout=180, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("refresh command failed to run: %s", exc)
            return False
        if proc.returncode != 0:
            log.warning("refresh command exited %s", proc.returncode)
            return False
        return True

    def trigger(self, *, require_rotation: bool = True) -> bool:
        """Attempt a refresh.

        With ``require_rotation`` (the default, used when the token is expired
        or the server returned 401) the ladder escalates until the stored token
        actually changes. Returns True only if a new token is now in place.
        """
        if not self.available():
            return False
        exe = claude_exe()
        if exe is None:
            return False
        self._last_attempt = time.monotonic()
        self.last_effective_step = None

        before = token_fingerprint(load())

        for name, tail in self._ladder():
            if not self._run(exe, tail):
                continue
            after = token_fingerprint(load())
            if after is not None and after != before:
                log.info("token rotated by 'claude %s'", " ".join(tail))
                self.last_effective_step = name
                return True
            if not require_rotation:
                # Nothing had expired, so no rotation was expected.
                self.last_effective_step = name
                return True
            log.info("'claude %s' did not rotate the token; escalating",
                     " ".join(tail))

        log.warning("no refresh command rotated the token; a Claude Code session "
                    "must be opened once to refresh it")
        return False
