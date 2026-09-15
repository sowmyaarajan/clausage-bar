"""The hover card image.

Pure Pillow, so all of this is testable without a window. The card exists
because the native tooltip is a 127-character plain-text field that can only
ever look like console output.
"""

from datetime import datetime, timedelta, timezone

from clausage_bar import card, freshness, theme
from clausage_bar.model import Eff, Snapshot, Spend

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def effs(session=17.0, weekly=31.0):
    return {"five_hour": Eff(pct=session,
                             next_reset=NOW + timedelta(hours=4, minutes=45)),
            "seven_day": Eff(pct=weekly, next_reset=NOW + timedelta(days=4))}


def snap(tier="example_tier", spend=True, source="api"):
    return Snapshot(
        captured_at=NOW, source=source, windows={},
        account={"seat_tier": tier} if tier else {},
        spend=Spend(percent=68.0, used_amount=34.00, limit_amount=50.0,
                    currency="USD") if spend else None)


def render(health=freshness.FRESH, age=120, **kw):
    return card.render(kw.pop("snapshot", snap()), kw.pop("effs", effs()),
                       health, age, now=NOW, **kw)


class TestShape:
    def test_it_is_transparent_outside_the_rounded_corners(self):
        """A square card would defeat the whole point of a layered window."""
        image = render()
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0

    def test_the_body_is_opaque(self):
        image = render()
        mid = (image.width // 2, image.height // 2)
        assert image.getpixel(mid)[3] == 255

    def test_it_grows_with_its_rows(self):
        two = render(snapshot=snap(spend=False))
        three = render()
        assert three.height > two.height

    def test_the_height_calculation_matches_what_is_drawn(self):
        """These drifted once and the sub-line landed on the next label."""
        image = render()
        expected = card.card_height(3) + 2 * card.SHADOW
        assert image.height == expected

    def test_there_is_room_for_a_shadow(self):
        assert card.SHADOW > 0
        image = render()
        assert image.width == card.WIDTH + 2 * card.SHADOW


class TestContent:
    def test_every_tracked_window_gets_a_row(self):
        rows = card.rows_for(snap(), effs(), NOW)
        assert [r.label for r in rows] == [
            theme.LONG_LABELS["five_hour"],
            theme.LONG_LABELS["seven_day"],
            theme.LONG_LABELS["credits"]]

    def test_rows_carry_the_shared_state(self):
        rows = card.rows_for(snap(), effs(session=95.0), NOW)
        assert rows[0].state == theme.state_for(95.0)

    def test_credits_show_the_money(self):
        rows = card.rows_for(snap(), effs(), NOW)
        assert rows[-1].sub == "$34.00 of $50.00"

    def test_a_rolled_over_window_says_so(self):
        rolled = effs()
        rolled["five_hour"] = Eff(pct=0.0, next_reset=None, inferred=True,
                                  note="window reset")
        rows = card.rows_for(snap(), rolled, NOW)
        assert rows[0].sub == "Window reset"

    def test_a_missing_reset_time_is_stated_not_faked(self):
        blind = effs()
        blind["five_hour"] = Eff(pct=12.0, next_reset=None)
        rows = card.rows_for(snap(), blind, NOW)
        assert "unknown" in rows[0].sub.lower()

    def test_no_reading_still_renders_a_card(self):
        """It must not raise, and it must not draw an empty box."""
        image = card.render(None, {}, freshness.UNKNOWN, float("inf"),
                            now=NOW)
        assert image.getpixel((image.width // 2, image.height // 2))[3] == 255

    def test_disabled_credits_are_labelled(self):
        import dataclasses
        broke = snap()
        # Spend is frozen, which is the right call for a parsed payload.
        broke.spend = dataclasses.replace(broke.spend, enabled=False)
        rows = card.rows_for(broke, effs(), NOW)
        assert "disabled" in rows[-1].sub


class TestNotAConsole:
    """The card replaced a tooltip that read as debug output."""

    def test_no_block_or_box_characters_anywhere(self):
        rows = card.rows_for(snap(), effs(), NOW)
        text = " ".join(r.label + r.sub for r in rows)
        for glyph in ("█", "░", "━", "▓", "▒", "›", "|"):
            assert glyph not in text, glyph

    def test_it_uses_no_monospace_face(self):
        """Monospace anywhere in the visual surfaces is what looked technical."""
        for weight, paths in theme.FONT_FILES.items():
            for path in paths:
                lowered = path.lower()
                assert "consol" not in lowered, path
                assert "courier" not in lowered, path

    def test_the_mark_is_drawn_not_typed(self):
        """Set as text the asterisk came out as a tofu box: Segoe has no U+2733."""
        import inspect
        source = inspect.getsource(card.render)
        assert "_mark(" in source
        assert "✳" not in source


class TestStale:
    def test_stale_differs_from_fresh(self):
        assert render().tobytes() != render(freshness.STALE, 2820).tobytes()

    def test_stale_says_not_current(self):
        """No shouty label -- just the wording and a muted palette."""
        import inspect
        source = inspect.getsource(card.render)
        assert "not current" in source
        assert '"STALE"' not in source and "'STALE'" not in source

    def test_stale_mutes_the_accents(self):
        for state in theme.STATE_ORDER:
            fresh = theme.accent(state)
            stale = theme.accent(state, stale=True)
            assert (max(stale) - min(stale)) < (max(fresh) - min(fresh))


class TestPaletteIsShared:
    def test_the_card_owns_no_colours_of_its_own(self):
        """Every colour must come from theme, or the surfaces drift."""
        import inspect
        source = inspect.getsource(card)
        # No bare RGB triples in the drawing code; the only literal tuples
        # allowed are alpha suffixes like "+ (255,)".
        import re
        triples = re.findall(r"\((\d{1,3}),\s*(\d{1,3}),\s*(\d{1,3})\)",
                             source)
        assert triples == [] or all(t == ("0", "0", "0") for t in triples), \
            triples

    def test_it_reads_the_shared_labels(self):
        import inspect
        assert "theme.LONG_LABELS" in inspect.getsource(card.rows_for)
