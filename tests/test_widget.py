"""Widget logic that can be tested without opening a window."""

from clausage_bar import widget


class TestFormatting:
    def test_percentages_are_whole_numbers_with_a_sign(self):
        assert widget.format_pct(48.0) == "48%"
        assert widget.format_pct(7.4) == "7%"
        assert widget.format_pct(99.6) == "100%"

    def test_missing_data_is_dashes_not_zero(self):
        """0% and "no reading" must not look the same."""
        assert widget.format_pct(None) == "--"
        assert widget.format_pct(0) == "0%"


class TestLevels:
    def test_it_defers_to_the_shared_bands(self):
        from clausage_bar import theme
        for pct in (0, 12, 49.9, 50, 74.9, 75, 89.9, 90, 100, None):
            assert widget.level_of(pct) == theme.state_for(pct)

    def test_it_owns_no_palette_of_its_own(self):
        assert not hasattr(widget, "LEVEL_HEX")

    def test_every_state_has_an_accent_colour(self):
        from clausage_bar import theme
        for state in theme.STATE_ORDER + (theme.UNKNOWN,):
            assert theme.accent_hex(state).startswith("#")
            assert theme.accent_hex(state, stale=True).startswith("#")


class TestSizing:
    def test_default_font_beats_the_ime_indicator(self):
        """ENG/IN glyphs measure ~8.7px; the point size here must exceed that.

        11pt Segoe UI is ~15px tall, so the widget lands near 56x44 against
        ENG/IN's 21x24 and the tray's hard 16x16 ceiling.
        """
        assert widget.FONT_SIZE >= 10

    def test_row_gap_gives_real_leading(self):
        """The 16px icon can only afford a 2px gutter; here it is deliberate."""
        assert widget.ROW_GAP >= 2

    def test_accent_bar_is_visible_width(self):
        assert widget.ACCENT_W >= 2
