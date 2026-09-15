"""Data shapes shared across providers and UI.

A window that could not be resolved is ``None`` -- deliberately distinct from
``0.0``, which means "resolved, and you have used none of it".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# Nominal window lengths, used only to project the *next* reset once a known
# reset time has already passed.
WINDOW_LENGTHS = {
    "five_hour": timedelta(hours=5),
    "seven_day": timedelta(days=7),
    "seven_day_opus": timedelta(days=7),
    "seven_day_sonnet": timedelta(days=7),
}

WINDOW_LABELS = {
    "five_hour": "5h",
    "seven_day": "7d",
    "seven_day_opus": "opus",
    "seven_day_sonnet": "sonnet",
}

# The two windows the user asked to be notified about.
PRIMARY_WINDOWS = ("five_hour", "seven_day")


def _parse_dt(value: Any) -> datetime | None:
    """Parse a timestamp into an aware UTC datetime.

    The two sources disagree on format, so both are handled:
      * the HTTP endpoint sends ISO-8601 with an offset
        ("2026-09-07T13:30:00.798800+00:00");
      * the status line sends a Unix epoch integer in *seconds*
        (1788787800). Milliseconds are accepted too, in case that changes.

    Naive datetimes would silently break both rollover inference and threshold
    re-arming, so anything without a tzinfo is assumed UTC explicitly.
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        # Anything past ~1973 in milliseconds would be year 5138 in seconds,
        # so the magnitude tells the two apart unambiguously.
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.lstrip("-").isdigit():          # numeric epoch arriving as a string
        return _parse_dt(int(text))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class WindowUsage:
    utilization: float
    resets_at: datetime | None = None
    raw_key_path: str | None = None
    derived: bool = False
    severity: str | None = None       # server-provided: normal | warning | ...
    is_active: bool | None = None
    locked_reason: str | None = None

    @classmethod
    def from_payload(cls, obj: Any, *, key_path: str | None = None) -> "WindowUsage | None":
        """Build from an endpoint or status-line fragment, tolerating key drift."""
        if not isinstance(obj, dict):
            return None
        pct = None
        derived = False
        for key in ("utilization", "percent", "used_percentage", "usedPercentage",
                    "used_pct", "percent_used"):
            candidate = obj.get(key)
            if isinstance(candidate, (int, float)):
                pct = float(candidate)
                break
        if pct is None:
            used, limit = obj.get("used"), obj.get("limit")
            if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit:
                pct = float(used) / float(limit) * 100.0
                derived = True
        if pct is None:
            return None
        resets = None
        for key in ("resets_at", "resetsAt", "reset_at", "resetAt"):
            resets = _parse_dt(obj.get(key))
            if resets:
                break
        return cls(utilization=pct, resets_at=resets, raw_key_path=key_path,
                   derived=derived, severity=obj.get("severity"),
                   is_active=obj.get("is_active"),
                   locked_reason=obj.get("locked_reason"))

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"utilization": round(self.utilization, 2)}
        out["resets_at"] = self.resets_at.isoformat() if self.resets_at else None
        if self.raw_key_path:
            out["raw_key_path"] = self.raw_key_path
        if self.derived:
            out["derived"] = True
        for key in ("severity", "is_active", "locked_reason"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    @classmethod
    def from_json(cls, obj: Any) -> "WindowUsage | None":
        if not isinstance(obj, dict):
            return None
        pct = obj.get("utilization")
        if not isinstance(pct, (int, float)):
            return None
        return cls(utilization=float(pct), resets_at=_parse_dt(obj.get("resets_at")),
                   raw_key_path=obj.get("raw_key_path"),
                   derived=bool(obj.get("derived")),
                   severity=obj.get("severity"), is_active=obj.get("is_active"),
                   locked_reason=obj.get("locked_reason"))


@dataclass(frozen=True)
class ExtraUsage:
    is_enabled: bool = False
    utilization: float | None = None
    used_credits: float | None = None
    monthly_limit: float | None = None

    @classmethod
    def from_payload(cls, obj: Any) -> "ExtraUsage | None":
        if not isinstance(obj, dict):
            return None

        def num(key: str) -> float | None:
            v = obj.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        return cls(is_enabled=bool(obj.get("is_enabled")), utilization=num("utilization"),
                   used_credits=num("used_credits"), monthly_limit=num("monthly_limit"))

    def to_json(self) -> dict[str, Any]:
        return {"is_enabled": self.is_enabled, "utilization": self.utilization,
                "used_credits": self.used_credits, "monthly_limit": self.monthly_limit}

    from_json = from_payload  # type: ignore[assignment]


@dataclass(frozen=True)
class Spend:
    """The extra-usage / spend limit, in real currency.

    Present when the account has extra usage enabled. `percent` is what the
    server itself reports, so we never recompute it from the minor units.
    """

    percent: float
    used_amount: float | None = None
    limit_amount: float | None = None
    currency: str = "USD"
    enabled: bool = True
    severity: str | None = None
    limit_reached: bool = False

    @staticmethod
    def _money(obj: Any) -> float | None:
        if not isinstance(obj, dict):
            return None
        minor = obj.get("amount_minor")
        if not isinstance(minor, (int, float)):
            return None
        exponent = obj.get("exponent")
        exponent = exponent if isinstance(exponent, int) else 2
        return float(minor) / (10 ** exponent)

    @classmethod
    def from_payload(cls, spend: Any, extra: Any = None) -> "Spend | None":
        """Prefer the `spend` object; fall back to `extra_usage`."""
        if isinstance(spend, dict) and isinstance(spend.get("percent"), (int, float)):
            return cls(
                percent=float(spend["percent"]),
                used_amount=cls._money(spend.get("used")),
                limit_amount=cls._money(spend.get("limit")),
                currency=(spend.get("used") or {}).get("currency", "USD"),
                enabled=bool(spend.get("enabled", True)),
                severity=spend.get("severity"),
                limit_reached=bool(spend.get("spend_limit_reached")),
            )
        if isinstance(extra, dict) and isinstance(extra.get("utilization"), (int, float)):
            exponent = extra.get("decimal_places")
            exponent = exponent if isinstance(exponent, int) else 2
            divisor = 10 ** exponent
            used = extra.get("used_credits")
            limit = extra.get("monthly_limit")
            return cls(
                percent=float(extra["utilization"]),
                used_amount=float(used) / divisor if isinstance(used, (int, float)) else None,
                limit_amount=float(limit) / divisor if isinstance(limit, (int, float)) else None,
                currency=extra.get("currency", "USD"),
                enabled=bool(extra.get("is_enabled", True)),
                limit_reached=bool(extra.get("spend_limit_reached")),
            )
        return None

    def to_json(self) -> dict[str, Any]:
        return {"percent": self.percent, "used_amount": self.used_amount,
                "limit_amount": self.limit_amount, "currency": self.currency,
                "enabled": self.enabled, "severity": self.severity,
                "limit_reached": self.limit_reached}

    @classmethod
    def from_json(cls, obj: Any) -> "Spend | None":
        if not isinstance(obj, dict) or not isinstance(obj.get("percent"), (int, float)):
            return None
        return cls(percent=float(obj["percent"]), used_amount=obj.get("used_amount"),
                   limit_amount=obj.get("limit_amount"),
                   currency=obj.get("currency", "USD"),
                   enabled=bool(obj.get("enabled", True)),
                   severity=obj.get("severity"),
                   limit_reached=bool(obj.get("limit_reached")))


@dataclass
class Snapshot:
    """A merged, normalized reading from whichever source won."""

    captured_at: datetime
    source: str                       # "api" | "statusline" | "cache"
    windows: dict[str, WindowUsage | None] = field(default_factory=dict)
    spend: Spend | None = None
    account: dict[str, Any] = field(default_factory=dict)
    model_name: str | None = None
    sources: list[str] = field(default_factory=list)

    def window(self, name: str) -> WindowUsage | None:
        return self.windows.get(name)

    def age_s(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.captured_at).total_seconds())

    def to_json(self) -> dict[str, Any]:
        from . import SCHEMA_VERSION
        return {
            "schema": SCHEMA_VERSION,
            "captured_at": self.captured_at.isoformat(),
            "source": self.source,
            "sources": self.sources,
            "windows": {k: (v.to_json() if v else None) for k, v in self.windows.items()},
            "spend": self.spend.to_json() if self.spend else None,
            "account": self.account,
            "model": self.model_name,
        }

    @classmethod
    def from_json(cls, obj: Any) -> "Snapshot | None":
        from . import SCHEMA_VERSION
        if not isinstance(obj, dict) or obj.get("schema") != SCHEMA_VERSION:
            return None
        captured = _parse_dt(obj.get("captured_at"))
        if not captured:
            return None
        raw_windows = obj.get("windows") or {}
        return cls(
            captured_at=captured,
            source=str(obj.get("source") or "cache"),
            windows={k: WindowUsage.from_json(v) for k, v in raw_windows.items()},
            spend=Spend.from_json(obj.get("spend")),
            account=obj.get("account") or {},
            model_name=obj.get("model"),
            sources=list(obj.get("sources") or []),
        )


@dataclass(frozen=True)
class Eff:
    """Effective (post-inference) state of one window."""

    pct: float | None
    next_reset: datetime | None = None
    inferred: bool = False
    note: str | None = None
