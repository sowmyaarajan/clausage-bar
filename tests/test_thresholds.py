from datetime import datetime, timedelta, timezone

import pytest

from clausage_bar.model import Eff, Spend
from clausage_bar.thresholds import ThresholdTracker, period_key

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
RESET = NOW + timedelta(hours=3)


@pytest.fixture
def tracker(tmp_path):
    t = ThresholdTracker(path=tmp_path / "notif.json")
    t.finish_cycle()          # leave cold-start so fresh data is not suppressed
    return t


def ev(tracker, pct, *, reset=RESET, age=0.0, name="five_hour"):
    return tracker.evaluate(name, Eff(pct=pct, next_reset=reset), age, NOW)


class TestPeriodKey:
    def test_subsecond_jitter_yields_one_key(self):
        """The server jitters microseconds; the key must ignore them.

        Observed live: the same window returned :00.353193, :00.543226 and
        :00.577929 on consecutive polls.
        """
        keys = {
            period_key("five_hour",
                       RESET.replace(microsecond=us), NOW)
            for us in (353193, 543226, 577929, 0)
        }
        assert len(keys) == 1

    def test_seconds_are_also_ignored(self):
        a = period_key("five_hour", RESET.replace(second=0), NOW)
        b = period_key("five_hour", RESET.replace(second=59), NOW)
        assert a == b

    def test_different_windows_differ(self):
        a = period_key("five_hour", RESET, NOW)
        b = period_key("five_hour", RESET + timedelta(hours=5), NOW)
        assert a != b

    def test_missing_reset_falls_back_to_bucket(self):
        key = period_key("five_hour", None, NOW)
        assert key.startswith("bucket:")


class TestFiring:
    def test_below_threshold_is_silent(self, tracker):
        assert ev(tracker, 10.0) is None
        assert ev(tracker, 49.9) is None

    def test_fires_once_at_fifty(self, tracker):
        assert ev(tracker, 52.0) == [50]
        assert ev(tracker, 52.0) is None
        assert ev(tracker, 60.0) is None

    def test_each_threshold_fires_separately(self, tracker):
        assert ev(tracker, 55.0) == [50]
        assert ev(tracker, 80.0) == [75]
        assert ev(tracker, 100.0) == [100]
        assert ev(tracker, 100.0) is None

    def test_jump_coalesces(self, tracker):
        assert ev(tracker, 82.0) == [50, 75]
        assert ev(tracker, 82.0) is None

    def test_jump_to_full_coalesces_all(self, tracker):
        assert ev(tracker, 100.0) == [50, 75, 100]

    def test_over_one_hundred_still_fires_once(self, tracker):
        assert ev(tracker, 140.0) == [50, 75, 100]
        assert ev(tracker, 150.0) is None

    def test_none_percentage_is_silent(self, tracker):
        assert ev(tracker, None) is None

    def test_dip_does_not_rearm(self, tracker):
        """Rounding jitter around 50.0 must not double-toast."""
        assert ev(tracker, 50.0) == [50]
        assert ev(tracker, 49.0) is None
        assert ev(tracker, 50.1) is None

    def test_windows_are_independent(self, tracker):
        assert ev(tracker, 60.0, name="five_hour") == [50]
        assert ev(tracker, 60.0, name="seven_day") == [50]


class TestRearming:
    def test_new_period_rearms(self, tracker):
        assert ev(tracker, 60.0) == [50]
        assert ev(tracker, 60.0, reset=RESET + timedelta(hours=5)) == [50]

    def test_jittered_same_period_does_not_rearm(self, tracker):
        assert ev(tracker, 60.0) == [50]
        jittered = RESET.replace(second=12, microsecond=987654)
        assert ev(tracker, 60.0, reset=jittered) is None

    def test_reset_clears_state(self, tracker):
        assert ev(tracker, 60.0) == [50]
        tracker.reset()
        assert ev(tracker, 60.0) == [50]


class TestPersistence:
    def test_state_survives_restart(self, tmp_path):
        path = tmp_path / "notif.json"
        first = ThresholdTracker(path=path)
        first.finish_cycle()
        assert first.evaluate("five_hour", Eff(60.0, RESET), 0.0, NOW) == [50]

        second = ThresholdTracker(path=path)
        second.finish_cycle()
        assert second.evaluate("five_hour", Eff(60.0, RESET), 0.0, NOW) is None

    def test_corrupt_state_does_not_crash(self, tmp_path):
        path = tmp_path / "notif.json"
        path.write_text("{not json", encoding="utf-8")
        t = ThresholdTracker(path=path)
        t.finish_cycle()
        assert t.evaluate("five_hour", Eff(60.0, RESET), 0.0, NOW) == [50]


class TestColdStart:
    def test_stale_data_suppressed_on_first_cycle(self, tmp_path):
        t = ThresholdTracker(path=tmp_path / "notif.json")   # cold start active
        assert t.evaluate("five_hour", Eff(90.0, RESET), 9 * 3600, NOW) is None

    def test_fresh_data_fires_on_first_cycle(self, tmp_path):
        t = ThresholdTracker(path=tmp_path / "notif.json")
        assert t.evaluate("five_hour", Eff(90.0, RESET), 10.0, NOW) == [50, 75]

    def test_suppressed_threshold_is_not_replayed_later(self, tmp_path):
        """Suppression must still record, or the toast arrives on cycle two."""
        t = ThresholdTracker(path=tmp_path / "notif.json")
        assert t.evaluate("five_hour", Eff(90.0, RESET), 9 * 3600, NOW) is None
        t.finish_cycle()
        assert t.evaluate("five_hour", Eff(90.0, RESET), 0.0, NOW) is None


class TestCredits:
    """The usage-credits pool: same alerts, but the period is inferred.

    Credits are the only number in the app denominated in real money, and
    before this they were displayed but never announced -- the cap could be
    reached in silence.
    """

    @staticmethod
    def _spend(percent, used=None, limit=50.0, enabled=True):
        return Spend(percent=percent, used_amount=used, limit_amount=limit,
                     currency="USD", enabled=enabled)

    def tracker(self, tmp_path):
        return ThresholdTracker(path=tmp_path / "notif.json")

    def test_fires_once_at_each_threshold(self, tmp_path):
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(52.0), 0) == [50]
        assert t.evaluate_credits(self._spend(53.0), 0) is None
        assert t.evaluate_credits(self._spend(76.0), 0) == [75]

    def test_a_jump_coalesces_into_one_announcement(self, tmp_path):
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(81.0), 0) == [50, 75]

    def test_a_dip_does_not_re_arm(self, tmp_path):
        """Rounding jitter around a threshold must not double-toast."""
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(50.4), 0) == [50]
        assert t.evaluate_credits(self._spend(49.6), 0) is None
        assert t.evaluate_credits(self._spend(50.1), 0) is None

    def test_a_real_reset_re_arms(self, tmp_path):
        """No resets_at exists, so a fall in usage is the evidence of a reset.

        An org whose billing month is not the calendar month would otherwise go
        weeks without re-arming.
        """
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(78.0), 0) == [50, 75]
        assert t.evaluate_credits(self._spend(2.0), 0) is None     # rolled over
        assert t.evaluate_credits(self._spend(55.0), 0) == [50]    # re-armed

    def test_a_raised_cap_re_arms(self, tmp_path):
        """A cap raised mid-month reads as a drop, and is worth alerting again."""
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(96.0, used=48.0), 0) == [50, 75]
        # limit 50 -> 100 halves the percentage without any reset
        assert t.evaluate_credits(self._spend(48.0, used=48.0, limit=100.0), 0) is None
        # A re-armed period is a fresh one, so 50 is crossed again too.
        assert t.evaluate_credits(self._spend(77.0, used=77.0, limit=100.0), 0) == [50, 75]

    def test_the_month_boundary_re_arms(self, tmp_path):
        t = self.tracker(tmp_path)
        t.finish_cycle()
        september = datetime(2026, 9, 20, tzinfo=timezone.utc)
        october = datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert t.evaluate_credits(self._spend(60.0), 0, september) == [50]
        assert t.evaluate_credits(self._spend(60.0), 0, october) == [50]

    def test_ignores_a_disabled_pool(self, tmp_path):
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(90.0, enabled=False), 0) is None

    def test_ignores_a_missing_pool(self, tmp_path):
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(None, 0) is None

    def test_cold_start_on_stale_data_is_suppressed(self, tmp_path):
        """"You are at 90%" off a nine-hour-old sample is worse than silence."""
        t = self.tracker(tmp_path)
        assert t.evaluate_credits(self._spend(90.0), 60_000) is None

    def test_suppressed_cold_start_does_not_replay(self, tmp_path):
        """The threshold is still recorded, so it cannot fire again later."""
        t = self.tracker(tmp_path)
        assert t.evaluate_credits(self._spend(90.0), 60_000) is None
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(90.0), 0) is None

    def test_state_survives_a_restart(self, tmp_path):
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(80.0), 0) == [50, 75]
        again = self.tracker(tmp_path)
        again.finish_cycle()
        assert again.evaluate_credits(self._spend(80.0), 0) is None

    def test_kept_separate_from_the_rate_limit_windows(self, tmp_path):
        """Credits at 80% must not consume the session window's 50/75."""
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(80.0), 0) == [50, 75]
        eff = Eff(pct=52.0, next_reset=datetime(2026, 9, 8, 15, 0,
                                                tzinfo=timezone.utc))
        assert t.evaluate("five_hour", eff, 0) == [50]

    def test_reset_menu_item_re_arms_credits_too(self, tmp_path):
        t = self.tracker(tmp_path)
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(80.0), 0) == [50, 75]
        t.reset()
        t.finish_cycle()
        assert t.evaluate_credits(self._spend(80.0), 0) == [50, 75]
