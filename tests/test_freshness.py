from datetime import datetime, timedelta, timezone

from clausage_bar import config, freshness
from clausage_bar.freshness import FRESH, STALE, UNKNOWN, effective, health
from clausage_bar.model import Snapshot, WindowUsage

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


class TestHealth:
    def test_recent_is_fresh(self):
        assert health(0) == FRESH
        assert health(config.FRESH_S - 1) == FRESH

    def test_middle_ages_are_stale(self):
        assert health(config.FRESH_S + 1) == STALE
        assert health(3600) == STALE
        assert health(config.DEAD_S - 1) == STALE

    def test_very_old_is_unknown(self):
        assert health(config.DEAD_S + 1) == UNKNOWN
        assert health(float("inf")) == UNKNOWN


class TestEffective:
    def test_missing_window_has_no_percentage(self):
        eff = effective(None, "five_hour", NOW)
        assert eff.pct is None
        assert eff.note == "no data"

    def test_live_window_passes_through(self):
        window = WindowUsage(utilization=42.0,
                             resets_at=NOW + timedelta(hours=2))
        eff = effective(window, "five_hour", NOW)
        assert eff.pct == 42.0
        assert eff.inferred is False
        assert eff.next_reset == NOW + timedelta(hours=2)

    def test_passed_reset_infers_zero(self):
        """The trust-critical case: a stale high reading whose window rolled."""
        window = WindowUsage(utilization=92.0,
                             resets_at=NOW - timedelta(hours=1))
        eff = effective(window, "five_hour", NOW)
        assert eff.pct == 0.0
        assert eff.inferred is True
        assert "reset" in eff.note

    def test_passed_reset_projects_next_boundary(self):
        window = WindowUsage(utilization=92.0,
                             resets_at=NOW - timedelta(hours=1))
        eff = effective(window, "five_hour", NOW)
        # 5h window: one length past the old boundary lands 4h ahead of now.
        assert eff.next_reset == NOW + timedelta(hours=4)
        assert eff.next_reset > NOW

    def test_very_old_reading_projects_forward_not_backward(self):
        window = WindowUsage(utilization=80.0,
                             resets_at=NOW - timedelta(days=30))
        eff = effective(window, "five_hour", NOW)
        assert eff.pct == 0.0
        assert eff.next_reset > NOW

    def test_weekly_uses_seven_day_length(self):
        window = WindowUsage(utilization=50.0,
                             resets_at=NOW - timedelta(days=1))
        eff = effective(window, "seven_day", NOW)
        assert eff.next_reset == NOW + timedelta(days=6)

    def test_no_reset_time_means_no_inference(self):
        window = WindowUsage(utilization=77.0, resets_at=None)
        eff = effective(window, "five_hour", NOW)
        assert eff.pct == 77.0
        assert eff.inferred is False
        assert eff.next_reset is None

    def test_exactly_at_reset_counts_as_rolled_over(self):
        window = WindowUsage(utilization=99.0, resets_at=NOW)
        assert effective(window, "five_hour", NOW).pct == 0.0


class TestEvaluateSnapshot:
    def test_none_snapshot_is_unknown(self):
        effs, hp, age = freshness.evaluate(None, NOW)
        assert effs == {}
        assert hp == UNKNOWN
        assert age == float("inf")

    def test_mixed_windows(self):
        snap = Snapshot(
            captured_at=NOW - timedelta(seconds=30),
            source="api",
            windows={
                "five_hour": WindowUsage(60.0, NOW + timedelta(hours=1)),
                "seven_day": WindowUsage(95.0, NOW - timedelta(minutes=5)),
                "seven_day_opus": None,
            },
        )
        effs, hp, age = freshness.evaluate(snap, NOW)
        assert hp == FRESH
        assert age == 30
        assert effs["five_hour"].pct == 60.0
        assert effs["seven_day"].pct == 0.0          # rolled over
        assert effs["seven_day"].inferred is True
        assert effs["seven_day_opus"].pct is None


class TestFormatting:
    def test_fmt_ago(self):
        assert freshness.fmt_ago(0) == "0s"
        assert freshness.fmt_ago(45) == "45s"
        assert freshness.fmt_ago(60) == "1m"
        assert freshness.fmt_ago(3600) == "1h00m"
        assert freshness.fmt_ago(3600 * 2 + 300) == "2h05m"
        assert freshness.fmt_ago(3600 * 24 * 6) == "6d"

    def test_fmt_until_past_is_none(self):
        assert freshness.fmt_until(NOW - timedelta(minutes=1), NOW) is None
        assert freshness.fmt_until(None, NOW) is None
        assert freshness.fmt_until(NOW + timedelta(hours=3), NOW) == "3h00m"
