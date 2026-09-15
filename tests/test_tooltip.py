from datetime import datetime, timedelta, timezone

from clausage_bar import app, config, tooltip
from clausage_bar.freshness import FRESH, STALE, UNKNOWN
from clausage_bar.model import Eff, Snapshot, Spend, WindowUsage

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def snap(**kw):
    base = dict(
        captured_at=NOW,
        source="api",
        windows={"five_hour": WindowUsage(9.0, NOW + timedelta(hours=4, minutes=34)),
                 "seven_day": WindowUsage(12.0, NOW + timedelta(days=3))},
        spend=Spend(percent=68.0, used_amount=34.00, limit_amount=50.0),
        account={"seat_tier": "example_tier"},
    )
    base.update(kw)
    return Snapshot(**base)


def effs(session=9.0, weekly=12.0, **kw):
    out = {
        "five_hour": Eff(pct=session,
                         next_reset=NOW + timedelta(hours=4, minutes=34)),
        "seven_day": Eff(pct=weekly, next_reset=NOW + timedelta(days=3)),
    }
    out.update(kw)
    return out


class TestBar:
    def test_empty_and_full(self):
        assert tooltip.bar(0) == "░" * 8
        assert tooltip.bar(100) == "█" * 8

    def test_proportional(self):
        assert tooltip.bar(50) == "█" * 4 + "░" * 4

    def test_small_nonzero_still_shows_a_cell(self):
        """3% must not render identically to 0%."""
        assert tooltip.bar(3).startswith("█")
        assert tooltip.bar(0.4).startswith("█")

    def test_none_is_all_empty(self):
        assert tooltip.bar(None) == "░" * 8

    def test_over_100_does_not_overflow(self):
        assert len(tooltip.bar(180)) == 8


class TestLengthInvariant:
    """szTip is a 128-character field; overflowing truncates unpredictably."""

    def test_normal_case_fits(self):
        text = tooltip.compose(snap(), effs(), FRESH, 30, now=NOW)
        assert len(text) <= config.TOOLTIP_MAX

    def test_everything_at_once_still_fits(self):
        full = effs()
        full["seven_day_opus"] = Eff(pct=88.0, next_reset=NOW + timedelta(days=2))
        full["seven_day_sonnet"] = Eff(pct=12.0, next_reset=NOW + timedelta(days=4))
        text = tooltip.compose(
            snap(), full, STALE, 47 * 60,
            note="token expired - open Claude Code once", now=NOW)
        assert len(text) <= config.TOOLTIP_MAX

    def test_absurd_note_is_truncated(self):
        text = tooltip.compose(snap(), effs(), FRESH, 30, note="x" * 500, now=NOW)
        assert len(text) <= config.TOOLTIP_MAX

    def test_both_windows_survive_dropping(self):
        text = tooltip.compose(snap(), effs(), FRESH, 30, note="y" * 400, now=NOW)
        assert "Session" in text
        assert "Weekly" in text
        assert len(text) <= config.TOOLTIP_MAX

    def test_three_digit_percentages_fit(self):
        text = tooltip.compose(snap(), effs(session=100.0, weekly=100.0),
                               FRESH, 30, now=NOW)
        assert len(text) <= config.TOOLTIP_MAX
        assert "100%" in text


class TestContent:
    def test_labels_both_windows_by_name(self):
        """The old single number was ambiguous about which window it meant."""
        text = tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)
        assert "Session" in text
        assert "Weekly" in text
        assert "9%" in text
        assert "12%" in text

    def test_is_multiline(self):
        text = tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)
        assert text.count("\n") >= 2

    def test_no_block_characters(self):
        """The meters made the tooltip read like debug output.

        They were affordable inside the 127-character cap; they were
        removed because a number and a clock time are what the eye
        wants from something visible for two seconds.
        """
        text = tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)
        for glyph in ("█", "░", "━", "▓", "▒"):
            assert glyph not in text, glyph

    def test_rows_share_a_percentage_column(self):
        """The aligned right edge is what makes three rows scannable."""
        text = tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)
        columns = []
        for label in ("Session", "Weekly", "Credits"):
            line = [l for l in text.split("\n")
                    if label in l][0]
            columns.append(line.index("%"))
        # The badge marker occupies its own column, so the numbers stay
        # aligned whether or not a row carries it.
        assert len(set(columns)) == 1, columns

    def test_the_badge_row_is_marked(self):
        """Hovering has to explain the number in the taskbar."""
        from clausage_bar import tooltip as tt
        text = tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)
        rows = [l for l in text.split("\n")
                if any(k in l for k in ("Session", "Weekly"))]
        marked = [l for l in rows if l.startswith(tt.BADGE_MARK)]
        assert len(marked) == 1, rows

    def test_the_marked_row_is_always_the_session(self):
        """The badge means one window, so the marker points at one row."""
        from clausage_bar import icon, tooltip as tt
        loaded = effs()
        loaded["seven_day"] = Eff(pct=91.0,
                                  next_reset=NOW + timedelta(days=2))
        text = tooltip.compose(snap(), loaded, FRESH, 90, now=NOW)
        marked = [l for l in text.split("\n")
                  if l.startswith(tt.BADGE_MARK)][0]
        assert "Session" in marked
        assert icon.badge_window(9.0, 91.0) == "five_hour"

    def test_credits_are_never_marked(self):
        """Credits are not a rate-limit window and never drive the badge."""
        from clausage_bar import tooltip as tt
        text = tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)
        credits_line = [l for l in text.split("\n")
                        if "Credits" in l][0]
        assert not credits_line.startswith(tt.BADGE_MARK)

    def test_it_is_titled(self):
        text = tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)
        assert text.split("\n")[0].startswith("Claude Usage")

    def test_shows_age(self):
        assert "Updated 1m ago" in tooltip.compose(snap(), effs(), FRESH,
                                                   90, now=NOW)

    def test_shows_the_source(self):
        """Which of the two sources produced this, in the user's words."""
        assert "api" in tooltip.compose(snap(), effs(), FRESH, 90, now=NOW)

    def test_stale_is_marked(self):
        text = tooltip.compose(snap(), effs(), STALE, 47 * 60, now=NOW)
        assert "stale" in text.lower()
        assert "Last seen" in text

    def test_fresh_is_not_marked(self):
        text = tooltip.compose(snap(), effs(), FRESH, 10, now=NOW)
        assert "stale" not in text.lower()
        assert "Updated" in text

    def test_rolled_over_window_is_labelled(self):
        rolled = effs()
        rolled["five_hour"] = Eff(pct=0.0, next_reset=NOW + timedelta(hours=4),
                                  inferred=True, note="window reset 1h ago")
        text = tooltip.compose(snap(), rolled, STALE, 3600, now=NOW)
        session_line = [l for l in text.split("\n") if "Session" in l][0]
        assert "reset" in session_line

    def test_no_data_message(self):
        text = tooltip.compose(None, {}, UNKNOWN, float("inf"), now=NOW)
        assert "No reading yet" in text
        assert len(text) <= config.TOOLTIP_MAX

    def test_note_shown_when_no_data(self):
        text = tooltip.compose(None, {}, UNKNOWN, float("inf"),
                               note="signed out", now=NOW)
        assert "signed out" in text

    def test_credits_included_when_room_allows(self):
        text = tooltip.compose(snap(), effs(), FRESH, 30, now=NOW)
        assert "Credits" in text

    def test_credits_omitted_when_disabled(self):
        s = snap(spend=Spend(percent=0.0, enabled=False))
        assert "Credits" not in tooltip.compose(s, effs(), FRESH, 30, now=NOW)

    def test_missing_window_is_skipped_not_zeroed(self):
        partial = {"five_hour": Eff(pct=9.0, next_reset=NOW + timedelta(hours=4)),
                   "seven_day": Eff(pct=None)}
        text = tooltip.compose(snap(), partial, FRESH, 30, now=NOW)
        assert "Session" in text
        assert "Weekly" not in text


class TestDetails:
    def test_lists_windows_and_spend(self):
        text = tooltip.details(snap(), effs(), FRESH, 30, now=NOW)
        assert "Current session" in text
        assert "9.0% used" in text
        assert "Weekly, all models" in text
        assert "Usage credits" in text
        assert "$34.00 of $50.00" in text

    def test_shows_tier(self):
        text = tooltip.details(snap(), effs(), FRESH, 30, now=NOW)
        assert "Example Tier" in text

    def test_shows_reset_times(self):
        text = tooltip.details(snap(), effs(), FRESH, 30, now=NOW)
        assert "resets" in text
        assert "in 4h34m" in text

    def test_no_data_case(self):
        text = tooltip.details(None, {}, UNKNOWN, float("inf"),
                               note="no credentials", now=NOW)
        assert "No data yet." in text
        assert "no credentials" in text

    def test_inferred_reset_is_disclosed(self):
        rolled = effs()
        rolled["five_hour"] = Eff(pct=0.0, next_reset=NOW + timedelta(hours=4),
                                  inferred=True,
                                  note="window reset 1h12m ago, assumed")
        text = tooltip.details(snap(), rolled, STALE, 3600, now=NOW)
        assert "assumed" in text


class TestToastCopy:
    """The notification wording. Small things, but they are what the user reads."""

    def test_money_uses_a_symbol_when_known(self):
        assert app.money(34.00, "USD") == "$34.00"
        assert app.money(1234.5, "EUR") == "\u20ac1,234.50"

    def test_money_falls_back_to_the_code(self):
        assert app.money(9.5, "CHF") == "CHF 9.50"
        assert app.money(9.5, "") == "9.50"

    def test_money_groups_thousands(self):
        assert app.money(12345.0, "USD") == "$12,345.00"

    def test_one_threshold_reads_plainly(self):
        assert app.join_thresholds([50]) == "50%"

    def test_two_thresholds_are_joined_with_and(self):
        assert app.join_thresholds([50, 75]) == "50% and 75%"

    def test_three_thresholds_do_not_stutter(self):
        """A naive join produced "50% and 75% and 100%"."""
        assert app.join_thresholds([50, 75, 100]) == "50%, 75% and 100%"
