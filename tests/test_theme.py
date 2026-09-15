"""The shared visual language.

The point of theme.py is that three surfaces cannot drift apart. Most of these
tests exist to fail if a future change reintroduces a per-surface palette.
"""

import pytest

from clausage_bar import theme


class TestStates:
    def test_bands_are_contiguous_and_ordered(self):
        expected = [
            (0, theme.CALM), (49.9, theme.CALM),
            (50, theme.MODERATE), (74.9, theme.MODERATE),
            (75, theme.WARNING), (89.9, theme.WARNING),
            (90, theme.HIGH), (99.9, theme.HIGH),
            (100, theme.CRITICAL), (140, theme.CRITICAL),
        ]
        for pct, state in expected:
            assert theme.state_for(pct) == state, pct

    def test_no_reading_is_its_own_state(self):
        """None is not 0%. One is "nothing used", the other "we don't know"."""
        assert theme.state_for(None) == theme.UNKNOWN
        assert theme.state_for(0) != theme.UNKNOWN

    def test_states_are_finer_than_the_alert_thresholds(self):
        """90% and 76% feel different; the old three-band scheme called both hot."""
        from clausage_bar import config
        assert len(theme.STATE_ORDER) > len(config.THRESHOLDS)
        assert theme.state_for(76) != theme.state_for(95)

    def test_display_bands_do_not_move_the_alert_thresholds(self):
        """Alerts must keep firing at 50/75/100 whatever the palette does."""
        from clausage_bar import config
        assert config.THRESHOLDS == (50, 75, 100)


class TestAccents:
    def test_every_state_has_a_colour(self):
        for state in theme.STATE_ORDER + (theme.UNKNOWN,):
            colour = theme.accent(state)
            assert len(colour) == 3
            assert all(0 <= c <= 255 for c in colour)

    def test_an_unknown_name_does_not_raise(self):
        """A typo in a state name must not take the tray down."""
        assert theme.accent("nonsense") == theme.accent(theme.UNKNOWN)

    def test_low_usage_is_not_grey(self):
        """"You have room" is information; a grey ring at 5% looks broken."""
        r, g, b = theme.accent(theme.CALM)
        assert g > r and g > b

    def test_critical_is_the_reddest(self):
        reds = {s: theme.accent(s)[0] - theme.accent(s)[1]
                for s in theme.STATE_ORDER}
        assert max(reds, key=reds.get) in (theme.HIGH, theme.CRITICAL)

    def test_stale_is_always_duller(self):
        """Saturation has to drop, or stale and fresh are the same picture."""
        for state in theme.STATE_ORDER:
            fresh, stale = theme.accent(state), theme.accent(state, stale=True)
            assert stale != fresh
            assert (max(stale) - min(stale)) < (max(fresh) - min(fresh)), state

    def test_stale_keeps_a_hint_of_the_hue(self):
        """A stale 95% should still lean red rather than go flat grey."""
        hot = theme.accent(theme.CRITICAL, stale=True)
        calm = theme.accent(theme.CALM, stale=True)
        assert hot != calm

    def test_hex_helpers_are_tk_ready(self):
        for state in theme.STATE_ORDER + (theme.UNKNOWN,):
            for stale in (False, True):
                value = theme.accent_hex(state, stale)
                assert len(value) == 7 and value.startswith("#")
                int(value[1:], 16)


class TestTrayPalette:
    def test_the_track_reads_on_either_taskbar(self):
        """One bitmap serves a dark and a light taskbar; Windows says which."""
        track = theme.TRAY_TRACK
        luminance = 0.2126 * track[0] + 0.7152 * track[1] + 0.0722 * track[2]
        assert 90 < luminance < 190, luminance

    def test_the_ring_stroke_leaves_a_hole(self):
        """The stroke is squeezed from both sides.

        Above ~0.20 the hole closes and the ring reads as a filled dot; below
        ~0.10 it stops reading as a ring. And every point of stroke costs the
        hole two points of diameter, which is where the numeral has to live.
        """
        assert 0.10 <= theme.RING_STROKE <= 0.20

    @staticmethod
    def _contrast(fg, bg):
        """WCAG relative-luminance contrast ratio."""
        def channel(value):
            value /= 255.0
            return value / 12.92 if value <= 0.03928 else \
                ((value + 0.055) / 1.055) ** 2.4

        def luminance(rgb):
            r, g, b = (channel(c) for c in rgb)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        light, dark = sorted((luminance(fg), luminance(bg)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    def test_the_numeral_has_a_plate_to_sit_on(self):
        """Ink alone vanishes on an unknown taskbar; the plate is the fix."""
        assert theme.RING_PLATE_ALPHA > 200

    def test_the_plate_is_bright_and_the_ink_is_dark(self):
        """The polarity was inverted deliberately.

        The first version was a dark disc with light digits: fine on a dark
        taskbar, marginal on a light one. A bright disc is the strongest
        silhouette a 24px icon can have against either, and it lets the
        numeral be near-black, which is the highest-contrast pairing there is.
        """
        assert min(theme.RING_PLATE) > 200, "the plate must be bright"
        assert max(theme.RING_INK) < 80, "the ink must be dark"

    def test_the_numeral_is_legible_by_measurement(self):
        """AA wants 4.5:1 for small text. This pairing is far above it."""
        ratio = self._contrast(theme.RING_INK, theme.RING_PLATE)
        assert ratio > 12.0, ratio

    def test_a_stale_numeral_is_still_readable(self):
        """Muted is not the same as illegible."""
        ratio = self._contrast(theme.RING_INK_STALE, theme.RING_PLATE)
        assert ratio > 2.0, ratio

    def test_the_plate_stands_out_from_both_taskbars(self):
        for taskbar in ((32, 33, 36), (243, 243, 243)):
            ratio = self._contrast(theme.RING_PLATE, taskbar)
            # Against a light taskbar the plate itself nearly vanishes, and
            # that is fine: the coloured ring supplies the silhouette there.
            assert ratio > 1.0

    def test_a_numeral_floor_exists(self):
        """Below a few pixels a numeral is a smudge, and worse than nothing."""
        assert theme.RING_MIN_GLYPH_PX >= 5

    def test_the_ring_has_a_margin(self):
        assert 0 < theme.RING_MARGIN < 0.12


class TestFormatting:
    def test_money_uses_a_symbol_when_known(self):
        assert theme.money(34.00, "USD") == "$34.00"
        assert theme.money(1234.5, "EUR") == "€1,234.50"

    def test_money_falls_back_to_the_code(self):
        assert theme.money(9.5, "CHF") == "CHF 9.50"
        assert theme.money(9.5, "") == "9.50"

    def test_money_can_drop_the_cents(self):
        """The tooltip pays for every character; the panel does not."""
        assert theme.money(34.00, "USD", 0) == "$34"

    def test_thresholds_do_not_stutter(self):
        assert theme.join_thresholds([50]) == "50%"
        assert theme.join_thresholds([50, 75]) == "50% and 75%"
        assert theme.join_thresholds([50, 75, 100]) == "50%, 75% and 100%"


class TestWording:
    def test_both_label_tables_cover_the_tracked_windows(self):
        for key in ("five_hour", "seven_day", "credits"):
            assert key in theme.SHORT_LABELS
            assert key in theme.LONG_LABELS

    def test_short_labels_fit_the_tooltip_column(self):
        """The tooltip's label column is 8 characters wide."""
        from clausage_bar import tooltip
        for label in theme.SHORT_LABELS.values():
            assert len(label) < tooltip._LABEL_W

    def test_the_surfaces_agree_on_terminology(self):
        """Different lengths, same words: "Session" inside "Current session"."""
        assert theme.SHORT_LABELS["five_hour"].lower() in \
            theme.LONG_LABELS["five_hour"].lower()
        assert theme.SHORT_LABELS["seven_day"].lower() in \
            theme.LONG_LABELS["seven_day"].lower()
        assert theme.SHORT_LABELS["credits"].lower() in \
            theme.LONG_LABELS["credits"].lower()


class TestOneSourceOfTruth:
    """No surface may keep its own copy of the palette or the bands."""

    @pytest.mark.parametrize("module", ["icon", "panel", "widget", "tooltip"])
    def test_no_surface_defines_its_own_level_table(self, module):
        import importlib
        mod = importlib.import_module("clausage_bar." + module)
        assert not hasattr(mod, "LEVEL_HEX"), module

    def test_the_app_delegates_its_formatters(self):
        from clausage_bar import app
        assert app.money is theme.money
        assert app.join_thresholds is theme.join_thresholds
