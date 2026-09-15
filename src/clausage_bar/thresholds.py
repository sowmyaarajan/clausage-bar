"""Threshold crossing state machine.

Guarantees:
  * Each threshold fires at most once per rate-limit period. The period key is
    ``resets_at``, so a new window re-arms with no timer logic of our own.
  * A jump that crosses several thresholds at once records all of them but
    emits a single toast naming them.
  * A dip inside the same period never re-arms a fired threshold, so rounding
    jitter around 50.0 cannot double-toast.
  * State is persisted after every mutation, so a crash or Quit does not
    replay toasts on restart.
  * The usage-credits pool gets the same treatment, but its period has to be
    inferred: see ``evaluate_credits``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config
from .atomicjson import read_json, write_atomic
from .logging_setup import get
from .model import WINDOW_LENGTHS, Eff

log = get("thresholds")


def period_key(name: str, next_reset: datetime | None, now: datetime) -> str:
    """A stable identity for the current rate-limit window.

    The server returns ``resets_at`` with jittering sub-second precision -- the
    same window came back as ...:00.353193, ...:00.543226 and ...:00.577929 on
    three consecutive polls. Keying on the raw string would make every poll
    look like a fresh window and re-arm every threshold, so the timestamp is
    truncated to the minute. Real reset boundaries are minute-aligned.
    """
    if next_reset is None:
        return _fallback_key(name, now)
    return next_reset.replace(second=0, microsecond=0).isoformat()


def _fallback_key(name: str, now: datetime) -> str:
    """Wall-clock bucket, used only when resets_at is unavailable."""
    length = WINDOW_LENGTHS.get(name)
    seconds = int(length.total_seconds()) if length else 5 * 3600
    return "bucket:{0}".format(int(now.timestamp()) // seconds)


class ThresholdTracker:
    def __init__(self, path=None, thresholds=config.THRESHOLDS) -> None:
        self.path = path or config.NOTIF_STATE_FILE
        self.thresholds = tuple(sorted(thresholds))
        raw = read_json(self.path, default={})
        self.store: dict[str, dict] = raw if isinstance(raw, dict) else {}
        self._cold_start = True

    # ---------------------------------------------------------------- state

    def _record(self, name: str) -> dict:
        rec = self.store.get(name)
        if not isinstance(rec, dict):
            rec = {"period_key": None, "fired": []}
            self.store[name] = rec
        rec.setdefault("period_key", None)
        if not isinstance(rec.get("fired"), list):
            rec["fired"] = []
        return rec

    def save(self) -> None:
        write_atomic(self.path, self.store)

    def reset(self) -> None:
        self.store = {}
        self.save()
        log.info("notification state cleared; all thresholds re-armed")

    # ---------------------------------------------------------------- logic

    def evaluate(self, name: str, eff: Eff, age_s: float,
                 now: datetime | None = None) -> list[int] | None:
        """Return the thresholds newly crossed, or None if nothing to announce."""
        now = now or datetime.now(timezone.utc)
        key = period_key(name, eff.next_reset, now)
        rec = self._record(name)

        if rec["period_key"] != key:
            if rec["period_key"] is not None:
                log.info("%s: new rate-limit period, thresholds re-armed", name)
            rec["period_key"] = key
            rec["fired"] = []

        if eff.pct is None:
            return None

        crossed = [t for t in self.thresholds
                   if eff.pct >= t and t not in rec["fired"]]
        if not crossed:
            return None

        # Record every crossed threshold, so a later poll does not re-announce
        # the ones we skipped past.
        rec["fired"].extend(crossed)
        rec["fired"] = sorted(set(rec["fired"]))
        self.save()

        # Toasting "you are at 90%" off a nine-hour-old sample is worse than
        # silence, so suppress the very first evaluation when data is stale.
        if self._cold_start and age_s > config.COLD_START_MAX_AGE_S:
            log.info("%s: suppressed cold-start notification for %s (data %.0fs old)",
                     name, crossed, age_s)
            return None

        return crossed

    # ------------------------------------------------------------- credits

    def evaluate_credits(self, spend, age_s: float,
                         now: datetime | None = None) -> list[int] | None:
        """Same 50/75/100 alerts for the usage-credits pool.

        Credits need their own path because the endpoint sends no ``resets_at``
        for them, so there is no period key to read off the server. Two signals
        are combined, because neither is sufficient alone:

          * **The calendar month (UTC).** A pool called ``monthly_limit``
            normally rolls at a month boundary.
          * **A fall in the used percentage.** If the org's billing month is
            not the calendar month -- a cap that resets on the 15th, say --
            month-keying alone would leave the thresholds un-rearmed for weeks
            after a real reset. A drop is direct evidence of the reset, whenever
            it happens.

        The month key alone would also never re-arm if the cap were raised
        mid-month, which reads as a drop and is worth alerting on again.
        """
        if spend is None or not getattr(spend, "enabled", False):
            return None
        if not isinstance(getattr(spend, "percent", None), (int, float)):
            return None

        now = now or datetime.now(timezone.utc)
        name = config.CREDITS_WINDOW
        rec = self._record(name)
        pct = float(spend.percent)

        month = "{0:04d}-{1:02d}".format(now.year, now.month)
        epoch = rec.get("epoch")
        epoch = epoch if isinstance(epoch, int) else 0
        last = rec.get("last_pct")
        if isinstance(last, (int, float)) and pct < last - config.CREDITS_RESET_DROP:
            epoch += 1
            log.info("credits: used fell %.1f%% -> %.1f%%, treating the pool as "
                     "reset and re-arming", last, pct)
        rec["epoch"] = epoch
        rec["last_pct"] = pct

        key = "credits:{0}:{1}".format(month, epoch)
        if rec["period_key"] != key:
            if rec["period_key"] is not None:
                log.info("credits: new period %s, thresholds re-armed", key)
            rec["period_key"] = key
            rec["fired"] = []

        crossed = [t for t in self.thresholds
                   if pct >= t and t not in rec["fired"]]
        if not crossed:
            self.save()             # the drop/epoch bookkeeping still matters
            return None

        rec["fired"].extend(crossed)
        rec["fired"] = sorted(set(rec["fired"]))
        self.save()

        if self._cold_start and age_s > config.COLD_START_MAX_AGE_S:
            log.info("credits: suppressed cold-start notification for %s "
                     "(data %.0fs old)", crossed, age_s)
            return None

        return crossed

    def finish_cycle(self) -> None:
        self._cold_start = False
