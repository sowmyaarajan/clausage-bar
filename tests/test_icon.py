from clausage_bar import config, icon


class TestBadgeText:
    def test_plain_percentages(self):
        assert icon.badge_text(0) == "0"
        assert icon.badge_text(9) == "9"
        assert icon.badge_text(12) == "12"
        assert icon.badge_text(99) == "99"

    def test_hundred_is_shown_honestly(self):
        """The narrow "1" is what lets three digits fit in 16px."""
        assert icon.badge_text(100) == "100"
        assert icon.badge_text(100.4) == "100"

    def test_rounds_to_nearest(self):
        assert icon.badge_text(11.6) == "12"
        assert icon.badge_text(11.4) == "11"

    def test_unknown_is_a_question_mark(self):
        assert icon.badge_text(None) == "?"

    def test_out_of_range_is_clamped(self):
        assert icon.badge_text(-5) == "0"
        assert icon.badge_text(10_000) == "999"


class TestLevels:
    def test_bands_match_the_notification_thresholds(self):
        assert icon.level_for(0) == "ok"
        assert icon.level_for(49.9) == "ok"
        assert icon.level_for(50) == "warn"
        assert icon.level_for(74.9) == "warn"
        assert icon.level_for(75) == "hot"
        assert icon.level_for(100) == "hot"
        assert icon.level_for(None) == "unknown"

    def test_every_level_has_a_band_colour(self):
        for level in ("ok", "warn", "hot", "unknown"):
            assert level in config.COLORS


class TestFonts:
    def test_both_faces_cover_every_digit(self):
        for font in (icon.FONT_LARGE, icon.FONT_SMALL):
            for char in "0123456789?":
                assert char in font

    def test_rows_are_consistent_within_a_glyph(self):
        for font in (icon.FONT_LARGE, icon.FONT_SMALL):
            height = icon._font_height(font)
            for char, rows in font.items():
                assert len(rows) == height, char
                widths = {len(r) for r in rows}
                assert len(widths) == 1, char
                for row in rows:
                    assert set(row) <= {"0", "1"}, char

    def test_one_is_narrower_than_the_other_digits(self):
        """A 5-wide "1" would push "100" to 17px and break the fit."""
        assert len(icon.FONT_LARGE["1"][0]) < len(icon.FONT_LARGE["0"][0])

    def test_one_has_a_flag_and_a_foot(self):
        """Without them it reads as a lowercase L and 100 looks like IOO."""
        glyph = icon.FONT_LARGE["1"]
        assert glyph[-1].count("1") >= 3          # foot serif
        assert glyph[1].count("1") > glyph[0].count("1")   # top flag

    def test_large_face_is_taller_than_the_fallback(self):
        assert icon._font_height(icon.FONT_LARGE) > icon._font_height(icon.FONT_SMALL)

    def test_widest_badge_fits_a_16px_icon(self):
        assert icon._text_width(icon.FONT_LARGE, "100", 1) <= 16

    def test_every_badge_character_is_renderable(self):
        for value in (None, 0, 7, 42, 100, 999):
            for char in icon.badge_text(value):
                assert char in icon.FONT_LARGE
                assert char in icon.FONT_SMALL


class TestFit:
    def test_prefers_the_large_face_when_it_fits(self):
        font, scale = icon._fit("12", 16, 7)
        assert font is icon.FONT_LARGE
        assert scale == 1

    def test_falls_back_when_the_box_is_tiny(self):
        font, _ = icon._fit("100", 12, 5)
        assert font is icon.FONT_SMALL

    def test_bigger_icons_get_bigger_glyphs(self):
        """A 24px icon at 150% scaling must use the pixels, not centre 16px art.

        The growth can come from a taller face or from an integer scale, so
        assert on the rendered glyph height rather than on the scale alone.
        """
        def glyph_height(size):
            gutter = max(2, round(size * 0.125))
            band = (size - gutter) // 2
            font, scale = icon._fit("12", size, band)
            return icon._font_height(font) * scale

        assert glyph_height(24) > glyph_height(16)
        assert glyph_height(32) >= glyph_height(24)

    def test_never_returns_an_overflowing_choice(self):
        for size in (16, 20, 24, 32, 48):
            band = (size - max(1, size // 16)) // 2
            for text in ("0", "12", "100", "?"):
                font, scale = icon._fit(text, size, band)
                assert icon._text_width(font, text, scale) <= size
                assert icon._font_height(font) * scale <= band


class TestRender:
    def test_size_is_honoured(self):
        for size in (16, 20, 24, 32):
            image, _ = icon.render_state(9, 12, size=size)
            assert image.size == (size, size)

    def test_two_row_mode_keeps_the_rows_distinct(self, monkeypatch):
        """In two-row mode session and weekly must not collapse together."""
        monkeypatch.setattr(icon, "STYLE", "light")
        monkeypatch.setattr(icon, "ICON_ROWS", 2)
        _, a = icon.render_state(9, 12)
        _, b = icon.render_state(12, 9)
        assert a != b

    def test_two_row_mode_captures_each_row_level(self, monkeypatch):
        monkeypatch.setattr(icon, "STYLE", "light")
        monkeypatch.setattr(icon, "ICON_ROWS", 2)
        _, key = icon.render_state(9, 80)
        assert key[1] == "ok"        # session
        assert key[3] == "hot"       # weekly

    def test_dimmed_differs_from_fresh(self):
        _, fresh = icon.render_state(9, 12, dimmed=False)
        _, stale = icon.render_state(9, 12, dimmed=True)
        assert fresh != stale
        assert icon.render(*fresh).tobytes() != icon.render(*stale).tobytes()

    def test_identical_input_is_cached(self):
        first, _ = icon.render_state(9, 12, size=16)
        second, _ = icon.render_state(9, 12, size=16)
        assert first is second      # lru_cache hit; avoids tray flicker

    @staticmethod
    def _colors(image):
        return {color for _count, color in image.getcolors(1 << 16)}

    def test_digits_are_white_in_both_styles(self):
        for style in ("bands", "dark"):
            image = icon.render("9", "ok", "12", "ok", False, 16, style)
            assert (255, 255, 255, 255) in self._colors(image), style

    def test_bands_style_fills_the_slot(self):
        """Filling the slot is the only way to look bigger at a fixed 16px.

        Checked away from the corners: the tile is rounded, so the very corner
        pixels are deliberately transparent.
        """
        image = icon.render("9", "ok", "12", "ok", False, 16, "bands",
                            "pixel", 2)
        assert image.getpixel((8, 0))[3] == 255
        assert image.getpixel((8, 15))[3] == 255
        assert image.getpixel((0, 8))[3] == 255

    def test_same_level_rows_are_still_separated(self):
        """Two green bands with no seam read as one solid block."""
        image = icon.render("9", "ok", "12", "ok", False, 16, "bands",
                            "pixel", 2)
        seam = [image.getpixel((x, 7)) for x in range(16)]
        band = image.getpixel((0, 2))
        assert any(px[:3] != band[:3] for px in seam)

    def test_each_band_takes_its_own_colour(self):
        image = icon.render("9", "ok", "91", "hot", False, 16, "bands",
                            "pixel", 2)
        top = image.getpixel((0, 1))[:3]
        bottom = image.getpixel((0, 14))[:3]
        assert top == config.COLORS["ok"]
        assert bottom == config.COLORS["hot"]

    def test_dark_style_uses_one_tile(self):
        image = icon.render("9", "ok", "91", "hot", False, 16, "dark",
                            "pixel", 2)
        assert image.getpixel((0, 1))[:3] == config.TILE_COLOR
        assert image.getpixel((0, 14))[:3] == config.TILE_COLOR

    def test_missing_data_renders_without_raising(self):
        image, key = icon.render_state(None, None)
        assert key[0] == "?" and key[2] == "?"
        assert image.size[0] > 0

    def test_one_window_missing_in_two_row_mode(self, monkeypatch):
        monkeypatch.setattr(icon, "STYLE", "light")
        monkeypatch.setattr(icon, "ICON_ROWS", 2)
        _, key = icon.render_state(None, 12)
        assert key[0] == "?"
        assert key[2] == "12"

    def test_tray_size_is_sane(self):
        assert 8 <= icon.tray_icon_size() <= 256


class TestStaleAcrossStyles:
    """Every style must make a stale reading visibly different.

    "dark" and "plain" have no band fill to dim, so the ink has to carry it;
    an earlier version left them pixel-identical to a fresh reading.
    """

    @staticmethod
    def _bytes(style, dimmed, family="pixel"):
        return icon.render("9", "ok", "12", "ok", dimmed, 16, style,
                           family).tobytes()

    def test_all_styles_show_staleness(self):
        for style in ("bands", "dark", "plain"):
            assert self._bytes(style, False) != self._bytes(style, True), style

    def test_all_styles_show_staleness_with_serif(self):
        for style in ("bands", "dark", "plain"):
            assert (self._bytes(style, False, "serif")
                    != self._bytes(style, True, "serif")), style


class TestTrueTypeFaces:
    def test_serif_and_sans_are_available(self):
        for family in ("serif", "sans"):
            assert icon._ttf(family, 10) is not None, family

    def test_fit_returns_a_size_that_fits(self):
        for text in ("9", "48", "100"):
            fitted = icon._ttf_fit("serif", text, 16, 7)
            assert fitted is not None, text
            _size, box = fitted
            assert box[2] - box[0] <= 16
            assert box[3] - box[1] <= 7

    def test_serif_renders_ink(self):
        """A thresholded serif at 7px must still leave pixels on."""
        image = icon.render("48", "ok", "19", "ok", False, 16, "dark", "serif")
        colors = {c for _n, c in image.getcolors(1 << 16)}
        assert (255, 255, 255, 255) in colors

    def test_unknown_family_falls_back_to_the_pixel_font(self):
        a = icon.render("48", "ok", "19", "ok", False, 16, "dark", "nonesuch")
        b = icon.render("48", "ok", "19", "ok", False, 16, "dark", "pixel")
        assert a.tobytes() == b.tobytes()


class TestTransparency:
    """The default style must be tile-less.

    Any background colour we choose differs slightly from the taskbar's own,
    and a 16x16 square of almost-right colour reads as a visible box. Only
    alpha 0 actually disappears.
    """

    def test_plain_background_is_fully_transparent(self):
        image = icon.render("48", "ok", "19", "ok", False, 16, "plain", "serif")
        for corner in ((0, 0), (15, 0), (0, 15), (15, 15)):
            assert image.getpixel(corner)[3] == 0, corner

    def test_plain_still_draws_opaque_ink(self):
        image = icon.render("48", "ok", "19", "ok", False, 16, "plain", "serif")
        alphas = {color[3] for _count, color in image.getcolors(1 << 16)}
        assert 0 in alphas and 255 in alphas

    def test_plain_ink_is_white_by_default(self):
        image = icon.render("48", "ok", "19", "ok", False, 16, "plain", "serif")
        colors = {c for _n, c in image.getcolors(1 << 16)}
        assert (255, 255, 255, 255) in colors

    def test_tiled_styles_are_opaque(self):
        """Sampled mid-edge: the rounded corners are transparent by design."""
        for style in ("light", "dark", "bands"):
            image = icon.render("48", "ok", "19", "ok", False, 16, style,
                                "serif", 1)
            assert image.getpixel((8, 0))[3] == 255, style
            assert image.getpixel((8, 15))[3] == 255, style

    def test_default_style_is_the_number(self):
        """The ring cost two thirds of the slot to say what the number says.

        A 24px ring leaves its numeral 9px of height; the same slot with no
        ring gives it 13px. State moved to the plate tint, so nothing was
        lost but the arc.
        """
        assert icon.STYLE == "number"
        assert icon.STYLE not in icon._DIGIT_STYLES

    def test_the_ring_is_still_available(self):
        """It was not deleted, only demoted -- the panel still uses it."""
        assert callable(icon.render_ring)
        assert callable(icon.render_gauge)


class TestSingleRowMode:
    """One number gets the whole 16px, which is the only way it is legible.

    Two rows leave 7px each against ENG/IN's ~8.7px, with no room for the ~7px
    of leading that makes ENG/IN readable. One row roughly doubles the glyph.
    """

    def test_it_is_the_default(self):
        assert icon.ICON_ROWS == 1

    def test_shows_the_worse_window(self, monkeypatch):
        """The digit badge keeps its own rule.

        Only the default badge changed to always mean the session; this
        older style still collapses to whichever window is worse, and its
        tests should say so rather than inherit the new contract.
        """
        monkeypatch.setattr(icon, "STYLE", "light")
        _, key = icon.render_state(48, 19)
        assert key[0] == "48"
        _, key = icon.render_state(9, 12)
        assert key[0] == "12"

    def test_level_follows_the_shown_number(self, monkeypatch):
        monkeypatch.setattr(icon, "STYLE", "light")
        _, key = icon.render_state(9, 80)
        assert key[0] == "80"
        assert key[1] == "hot"

    def test_falls_back_to_whichever_window_has_data(self):
        _, key = icon.render_state(None, 12)
        assert key[0] == "12"
        _, key = icon.render_state(48, None)
        assert key[0] == "48"

    def test_no_data_at_all(self):
        _, key = icon.render_state(None, None)
        assert key[0] == "?"

    def test_glyph_is_much_taller_than_in_two_row_mode(self):
        one = icon._ttf_fit("serif", "48", 16, 16)
        two = icon._ttf_fit("serif", "48", 16, 7)
        assert one is not None and two is not None
        one_h = one[1][3] - one[1][1]
        two_h = two[1][3] - two[1][1]
        assert one_h > two_h
        assert one_h >= 9        # ENG/IN's per-line glyphs measure ~8.7px

    def test_background_stays_transparent(self):
        image = icon.render("48", "ok", "48", "ok", False, 16, "plain",
                            "serif", 1)
        assert image.getpixel((0, 0))[3] == 0

    def test_stale_still_differs(self):
        for style in ("plain", "dark", "bands"):
            fresh = icon.render("48", "ok", "48", "ok", False, 16, style,
                                "serif", 1).tobytes()
            stale = icon.render("48", "ok", "48", "ok", True, 16, style,
                                "serif", 1).tobytes()
            assert fresh != stale, style


class TestSegmentFace:
    """Digits built from segments, because width is the binding constraint.

    Every TrueType face measured needs 20-30px to set two digits at a 16px
    inked height, since each glyph carries side bearings. Plotted glyphs have
    none, so two digits fit in 13px at the full height of the icon.
    """

    def test_two_digits_fill_a_16px_icon(self):
        face = icon._fit_segment("48", 14, 14)
        assert face is not None
        assert len(face["0"]) == 14                  # full height
        assert icon._text_width(face, "48") <= 14

    def test_three_digits_still_fit(self):
        face = icon._fit_segment("100", 14, 14)
        assert face is not None
        assert icon._text_width(face, "100") <= 14

    def test_taller_than_any_truetype_option(self):
        """The whole point: plotted glyphs beat a thresholded face here."""
        segment = len(icon._fit_segment("48", 14, 14)["0"])
        fitted = icon._ttf_fit("serif", "48", 16, 16)
        truetype = fitted[1][3] - fitted[1][1]
        assert segment > truetype

    def test_digits_are_uniform_width(self):
        """Tabular by construction, so the badge never jitters."""
        face = icon._fit_segment("48", 14, 14)
        widths = {len(face[d][0]) for d in "023456789"}
        assert len(widths) == 1

    def test_one_is_narrower(self):
        face = icon._fit_segment("48", 14, 14)
        assert len(face["1"][0]) < len(face["0"][0])

    def test_stroke_keeps_a_counter_open(self):
        """A 2px stroke in a 5px glyph closes the hole in 0 and 8."""
        for text in ("48", "100"):
            face = icon._fit_segment(text, 14, 14)
            zero = face["0"]
            middle = zero[len(zero) // 2]
            assert "0" in middle, text        # the counter is still open

    def test_no_data_is_a_single_bar(self):
        face = icon._fit_segment("?", 14, 14)
        lit = [row for row in face["?"] if "1" in row]
        assert 0 < len(lit) <= 3              # one centred bar, not a glyph

    def test_gives_up_gracefully_when_too_small(self):
        assert icon._fit_segment("48", 4, 4) is None


class TestContrast:
    """WCAG AA wants 4.5:1 for small text. Measured, not assumed."""

    @staticmethod
    def _ratio(fg, bg):
        def channel(v):
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        def lum(c):
            r, g, b = (channel(x) for x in c)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        a, b = lum(fg), lum(bg)
        hi, lo = max(a, b), min(a, b)
        return (hi + 0.05) / (lo + 0.05)

    def test_light_style_passes_aa_for_every_level(self):
        for level, tint in config.PALE_COLORS.items():
            assert self._ratio(config.INK_DARK, tint) >= 4.5, level

    def test_light_tile_stands_out_from_a_dark_taskbar(self):
        taskbar = (32, 33, 36)
        assert self._ratio(config.PALE_COLORS["ok"], taskbar) >= 4.5

    def test_dark_style_passes_aa(self):
        assert self._ratio((255, 255, 255), config.TILE_COLOR) >= 4.5

    def test_stale_ink_is_still_readable(self):
        assert self._ratio(config.INK_STALE,
                           config.PALE_COLORS["unknown"]) >= 3.0


class TestBadgePadding:
    """The digits must not touch the tile edge.

    This regressed twice. A full-bleed tile with a 1px inset put 14px-tall
    numerals in a 16px square, and the badge read as congested however good
    the glyphs themselves were -- so the padding is asserted, not eyeballed.
    """

    @staticmethod
    def _ink_box(image, tint):
        """Bounding box of the ink, found by masking out the tile colour."""
        pixels = image.convert("RGB").load()
        width, height = image.size
        xs, ys = [], []
        for y in range(height):
            for x in range(width):
                if image.getpixel((x, y))[3] and pixels[x, y] != tint:
                    xs.append(x)
                    ys.append(y)
        assert xs, "nothing was drawn"
        return min(xs), min(ys), max(xs), max(ys)

    def _measure(self, text, level="ok"):
        image = icon.render(text, level, text, level, False, 16,
                            "light", "serif", 1)
        tint = config.PALE_COLORS[level]
        return self._ink_box(image, tint)

    def test_vertical_air_on_both_sides(self):
        for text in ("9", "34", "100"):
            left, top, right, bottom = self._measure(text)
            assert top >= 3, (text, top)
            assert 15 - bottom >= 3, (text, bottom)

    def test_horizontal_air_on_both_sides(self):
        for text in ("9", "34", "100"):
            left, top, right, bottom = self._measure(text)
            assert left >= 2, (text, left)
            assert 15 - right >= 2, (text, right)

    def test_ink_is_centred(self):
        """Off-centre digits look like a rendering mistake, not a design."""
        for text in ("9", "34", "100"):
            left, _top, right, _bottom = self._measure(text)
            assert abs(left - (15 - right)) <= 1, text

    def test_digits_do_not_touch_each_other(self):
        """Two pixels between digits: the difference between 34 and a smear."""
        face, tracking = icon._fit_segment_spaced("34", 12, 10)
        assert face is not None
        assert tracking >= 2

    def test_glyphs_stay_heavy_despite_the_padding(self):
        """Air must not be bought with a thin stroke; weight beats size here."""
        face, _tracking = icon._fit_segment_spaced("34", 12, 10)
        top = face["0"][0]
        stem = face["0"][len(face["0"]) // 2]
        assert top.count("1") == len(top)             # solid bar
        assert stem.startswith("11") and stem.endswith("11")

    def test_tile_still_fills_the_slot(self):
        """The padding is inside the tile; the tile itself is full-bleed."""
        image = icon.render("34", "ok", "34", "ok", False, 16,
                            "light", "serif", 1)
        for corner in ((1, 8), (14, 8), (8, 1), (8, 14)):
            assert image.getpixel(corner)[3] == 255, corner


class TestSegmentLadder:
    """The face ladder is derived from the glyph height, not hard-coded.

    Because the badge is not always 16px: at 150% display scaling the tray
    slot is 24px, and a ladder tuned for a 10px glyph left 16px-tall digits
    with spindly 2px strokes.
    """

    @staticmethod
    def _stroke(face_height, width, one_width, stroke):
        return stroke

    def test_stroke_grows_with_the_glyph(self):
        small = icon._seg_ladder(10)[0][2]
        large = icon._seg_ladder(16)[0][2]
        assert large > small

    def test_widths_grow_with_the_glyph(self):
        assert icon._seg_ladder(16)[0][0] > icon._seg_ladder(10)[0][0]

    def test_every_rung_keeps_its_counter_open(self):
        """A stroke wide enough to close the hole in 0 turns it into a block."""
        for height in (8, 10, 12, 14, 16, 20, 24):
            for width, _one, stroke in icon._seg_ladder(height):
                assert width - 2 * stroke >= 1, (height, width, stroke)

    def test_ladder_is_ordered_widest_first(self):
        widths = [w for w, _o, _s in icon._seg_ladder(16)]
        assert widths == sorted(widths, reverse=True)

    def test_one_is_always_narrower_than_the_rest(self):
        for height in (10, 16, 24):
            for width, one, _stroke in icon._seg_ladder(height):
                assert one <= width, (height, width, one)

    def test_the_narrow_rung_exists_for_three_digits(self):
        """Dropping the narrowest rung once made 100% unrenderable."""
        for height, box in ((10, 12), (16, 18)):
            face, tracking = icon._fit_segment_spaced("100", box, height)
            assert face is not None, height
            assert icon._text_width(face, "100", 1, tracking) <= box


class TestDpiSizes:
    """A 24px slot must produce a 24px bitmap, not a scaled 16px one."""

    def test_badge_matches_the_requested_size(self):
        for size in (16, 20, 24, 32):
            image, key = icon.render_state(53.0, 25.0, size=size)
            assert image.size == (size, size)
            assert key[5] == size

    def test_a_bigger_slot_gets_bigger_glyphs(self):
        """The whole point of DPI awareness: use the pixels, do not stretch."""
        def ink_height(size):
            image = icon.render("53", "warn", "53", "warn", False, size,
                                "light", "serif", 1)
            tint = config.PALE_COLORS["warn"]
            rows = [y for y in range(size)
                    if any(image.getpixel((x, y))[:3] != tint
                           for x in range(size)
                           if image.getpixel((x, y))[3])]
            return max(rows) - min(rows) + 1

        assert ink_height(24) > ink_height(16)

    def test_padding_scales_with_the_slot(self):
        image = icon.render("53", "warn", "53", "warn", False, 24,
                            "light", "serif", 1)
        tint = config.PALE_COLORS["warn"]
        tops = [y for y in range(24)
                if any(image.getpixel((x, y))[:3] != tint
                       for x in range(24) if image.getpixel((x, y))[3])]
        assert min(tops) >= 3          # proportionally more air, not a fixed 1px


class TestRing:
    """The tray badge. Geometry here is easy to get subtly wrong.

    The caps were once placed on the arc's bounding box rather than on its
    centre line -- Pillow grows an arc's width inward -- which pushed them a
    half-stroke proud and turned every partial ring into a sausage. These
    tests pin down the properties that made that visible.
    """

    @staticmethod
    def _ink(image):
        """Opaque pixel count, ignoring the faint track."""
        return sum(1 for p in image.convert("RGBA").getchannel("A")
                   .point(lambda a: 255 if a > 200 else 0)
                   .tobytes() if p)

    @staticmethod
    def _ring(pct, dimmed=False, size=24):
        from clausage_bar import theme
        return icon.render_ring(pct, theme.state_for(pct), dimmed, size)

    def test_renders_at_every_real_tray_size(self):
        for size in (16, 20, 24, 32):
            assert self._ring(60, size=size).size == (size, size)

    def test_more_usage_means_more_arc(self):
        counts = [self._ink(self._ring(p)) for p in (10, 30, 60, 90)]
        assert counts == sorted(counts), counts

    def test_a_full_ring_is_the_most_ink(self):
        assert self._ink(self._ring(100)) > self._ink(self._ring(90))

    def test_zero_draws_no_arc_but_keeps_the_track(self):
        """0% is an empty meter, which is a reading -- not a blank icon."""
        from clausage_bar import theme
        bare = icon.render_ring(0, theme.CALM, False, 24, number=False)
        assert self._ink(bare) == 0                     # no accent arc
        assert max(bare.getchannel("A").tobytes()) > 0   # but a visible track

    def test_zero_still_says_zero(self):
        """The ring answers "roughly"; the numeral answers "how much"."""
        from clausage_bar import theme
        bare = icon.render_ring(0, theme.CALM, False, 24, number=False)
        numbered = icon.render_ring(0, theme.CALM, False, 24, number=True)
        assert numbered.tobytes() != bare.tobytes()

    def test_no_reading_differs_from_zero(self):
        assert self._ring(None).tobytes() != self._ring(0).tobytes()

    def test_a_sliver_of_usage_is_visible(self):
        """1% must not render identically to 0%."""
        assert self._ink(self._ring(1)) > 0

    def test_stale_is_visibly_different(self):
        for pct in (25, 60, 95):
            assert self._ring(pct, dimmed=True).tobytes() != \
                self._ring(pct).tobytes()

    def test_the_arc_starts_at_twelve_oclock(self):
        """A meter that starts anywhere else is not read as a meter."""
        ring = self._ring(25, size=48)
        width, mid = ring.size[0], ring.size[0] // 2
        top_band = [ring.getpixel((x, 3)) for x in range(width)]
        assert any(p[3] > 200 for p in top_band), "nothing lit at the top"
        # 25% sweeps clockwise from the top, so the right side is lit and the
        # left is bare.
        right = [ring.getpixel((width - 4, y)) for y in range(mid)]
        left = [ring.getpixel((3, y)) for y in range(mid)]
        assert sum(1 for p in right if p[3] > 200) > 0
        assert sum(1 for p in left if p[3] > 200) == 0

    def test_the_stroke_never_closes_the_hole(self):
        """If the stroke meets in the middle it stops being a ring."""
        from clausage_bar import theme
        for size in (16, 20, 24, 32):
            bare = icon.render_ring(100, theme.CRITICAL, False, size,
                                    number=False)
            centre = bare.getpixel((size // 2, size // 2))
            assert centre[3] == 0, size

    def test_the_numeral_fills_the_hole_not_the_ring(self):
        """The plate must stay inside the stroke, never under it."""
        from clausage_bar import theme
        size = 24
        numbered = icon.render_ring(60, theme.MODERATE, False, size,
                                    number=True)
        # The numbered ring uses its own thinner geometry -- measuring with
        # the bare constants would make this test pass by accident.
        hole = icon._ring_hole(size, size * theme.RING_MARGIN_NUMBERED,
                               max(1.0, size * theme.RING_STROKE_NUMBERED))
        # Just outside the hole, on the horizontal centre line, must still be
        # the ring's own stroke or bare slot -- not plate.
        edge_x = int((size - 1) / 2 + hole / 2 + 1)
        pixel = numbered.getpixel((min(edge_x, size - 1), size // 2))
        assert pixel[:3] != theme.RING_PLATE

    def test_the_numeral_is_dropped_when_it_would_be_a_smudge(self):
        """A 16px hole cannot hold a legible two-digit number, so it does not
        try. A bare ring is better than three grey pixels."""
        from clausage_bar import theme
        hole = icon._ring_hole(16, 16 * theme.RING_MARGIN_NUMBERED,
                               max(1.0, 16 * theme.RING_STROKE_NUMBERED))
        _w, h = icon._numeral_box(hole)
        assert h < theme.RING_MIN_GLYPH_PX
        small = icon.render_ring(60, theme.MODERATE, False, 16, number=True)
        bare = icon.render_ring(60, theme.MODERATE, False, 16, number=False)
        assert small.tobytes() == bare.tobytes()

    def test_bigger_slots_do_carry_the_numeral(self):
        from clausage_bar import theme
        for size in (20, 24, 32):
            numbered = icon.render_ring(60, theme.MODERATE, False, size,
                                        number=True)
            bare = icon.render_ring(60, theme.MODERATE, False, size,
                                    number=False)
            assert numbered.tobytes() != bare.tobytes(), size

    def test_a_numbered_ring_is_thinner_than_a_bare_one(self):
        """The digits state the value, so the ring only has to hint at it.

        That is where the numeral's room comes from: every point of stroke
        costs the hole two points of diameter. A bare ring stays bold because
        then the stroke is the only signal there is.
        """
        from clausage_bar import theme
        assert theme.RING_STROKE_NUMBERED < theme.RING_STROKE
        assert theme.RING_MARGIN_NUMBERED <= theme.RING_MARGIN

        # And prove the renderer honours it: the bare ring lays down more ink.
        bare = icon.render_ring(100, theme.CRITICAL, False, 24, number=False)
        numbered = icon.render_ring(100, theme.CRITICAL, False, 24,
                                    number=True)

        def accent_px(image):
            target = theme.accent(theme.CRITICAL)
            return sum(1 for x in range(24) for y in range(24)
                       if image.getpixel((x, y))[:3] == target
                       and image.getpixel((x, y))[3] > 200)

        assert accent_px(bare) > accent_px(numbered)

    def test_the_numeral_grows_with_the_slot(self):
        """A 32px slot must not centre 24px digits in it."""
        from clausage_bar import theme

        def glyph_height(size):
            hole = icon._ring_hole(size, size * theme.RING_MARGIN_NUMBERED,
                                   max(1.0, size * theme.RING_STROKE_NUMBERED))
            _w, h = icon._numeral_box(hole)
            face, _t = icon._fit_segment_spaced("81", _w, h)
            return len(face["0"]) if face else 0

        assert glyph_height(32) > glyph_height(24) > glyph_height(20)

    def test_the_numeral_beats_the_ime_indicator(self):
        """ENG/IN glyphs measure ~8.7px. At 24px the badge should match it."""
        from clausage_bar import theme
        hole = icon._ring_hole(24, 24 * theme.RING_MARGIN_NUMBERED,
                               max(1.0, 24 * theme.RING_STROKE_NUMBERED))
        _w, h = icon._numeral_box(hole)
        face, _t = icon._fit_segment_spaced("81", _w, h)
        assert face is not None
        assert len(face["0"]) >= 9

    def test_one_is_not_a_bare_bar(self):
        """Pure segments render 1 as a rule, so "100" came out as "IOO"."""
        face = icon._segment_face(5, 8, 2, 3)
        one = face["1"]
        assert one[0].count("1") > one[len(one) // 2].count("1"), "no flag"
        assert one[-1].count("1") > one[len(one) // 2].count("1"), "no foot"

    def test_edges_are_antialiased(self):
        """Drawn at 8x and downsampled: a hard-edged arc would be a staircase."""
        ring = self._ring(60, size=24)
        alpha = ring.getchannel("A").tobytes()
        partial = [a for a in alpha if 0 < a < 255]
        assert len(partial) > 10

    def test_it_is_cached(self):
        from clausage_bar import theme
        first = icon.render_ring(60, theme.state_for(60), False, 24)
        second = icon.render_ring(60, theme.state_for(60), False, 24)
        assert first is second

    def test_render_state_returns_a_ring_keyed_for_change_detection(self):
        _image, a = icon.render_state(60, 20, size=24)
        _image, b = icon.render_state(61, 20, size=24)
        _image, c = icon.render_state(60, 20, size=24)
        assert a != b
        assert a == c

    def test_it_shows_the_session(self):
        """Whatever the weekly window is doing -- the badge means one thing."""
        _image, key = icon.render_state(10, 88, size=24)
        assert key[0] == "10"
        from clausage_bar import theme
        assert key[1] == theme.CALM


class TestPanelGauge:
    """The panel's ring: the same renderer, large enough for a numeral."""

    def test_it_shares_the_arc_code(self):
        """Cohesion is the whole point; two arc routines would drift."""
        import inspect
        source = inspect.getsource(icon.render_gauge)
        assert "_draw_arc" in source

    def test_it_carries_the_percentage(self):
        """At 52px the numeral fits, which it cannot do at 16px."""
        from clausage_bar import theme
        plain = icon.render_gauge(60, theme.MODERATE, False, 52, numeral=False)
        labelled = icon.render_gauge(60, theme.MODERATE, False, 52,
                                     numeral=True)
        assert plain.tobytes() != labelled.tobytes()

    def test_the_numeral_never_touches_the_arc(self):
        """Plate must separate ink from arc, or it reads as a punched disc.

        This used to look for fully transparent pixels between the two. That
        stopped being the right measure once the plate filled the hole
        edge-to-edge with the stroke: there is no bare gap any more, and there
        should not be. What matters is that plate-coloured pixels lie between
        the accent and the ink.
        """
        from clausage_bar import theme
        for pct in (7, 60, 100):
            state = theme.state_for(pct)
            image = icon.render_gauge(pct, state, False, 52).convert("RGB")
            size = image.size[0]
            mid = size // 2
            row = [image.getpixel((x, mid)) for x in range(size)]

            def near(pixel, target, tolerance=26):
                return all(abs(pixel[i] - target[i]) <= tolerance
                           for i in range(3))

            plate = [i for i, px in enumerate(row)
                     if near(px, theme.RING_PLATE)]
            ink = [i for i, px in enumerate(row) if near(px, theme.RING_INK)]
            assert plate, ("no plate on the centre line", pct)
            if ink:
                # Every ink pixel sits inside the plate's span.
                assert min(plate) < min(ink) and max(ink) < max(plate), pct

    def test_stale_gauges_are_muted(self):
        from clausage_bar import theme
        fresh = icon.render_gauge(60, theme.MODERATE, False, 52)
        stale = icon.render_gauge(60, theme.MODERATE, True, 52)
        assert fresh.tobytes() != stale.tobytes()


class TestBadgeWindow:
    """Which window the badge stands for -- defined once, read by all three.

    Duplicating this rule was how the badge and the tooltip could disagree
    about the same reading.
    """

    def test_the_badge_is_always_the_session(self):
        """One meaning, always.

        It used to be whichever window was higher, so the number silently
        switched between the two and "33" could be either -- with no way to
        tell which from the taskbar. A badge that always means the same thing
        beats one that always shows the larger number.
        """
        assert icon.badge_window(83, 28) == "five_hour"
        assert icon.badge_window(10, 88) == "five_hour"
        assert icon.badge_window(55, 55) == "five_hour"
        assert icon.badge_window(0, 99) == "five_hour"

    def test_one_missing_window_falls_back_to_the_other(self):
        assert icon.badge_window(None, 40) == "seven_day"
        assert icon.badge_window(40, None) == "five_hour"

    def test_no_data_at_all_selects_nothing(self):
        assert icon.badge_window(None, None) is None

    def test_zero_is_a_reading_not_a_gap(self):
        """0% must still be selectable; None is the only "no reading"."""
        assert icon.badge_window(0, None) == "five_hour"
        assert icon.badge_window(None, 0) == "seven_day"

    def test_the_badge_agrees_with_the_selection(self):
        for session, weekly in ((83, 28), (10, 88), (None, 40), (55, 55)):
            shown = icon.badge_window(session, weekly)
            expected = {"five_hour": session, "seven_day": weekly}[shown]
            _image, key = icon.render_state(session, weekly, size=24)
            assert key[0] == icon.badge_text(expected)

    def test_the_key_records_which_window_is_shown(self):
        """So --diagnose can say it, and a change of window redraws."""
        _image, key = icon.render_state(27, 33, size=24)
        assert key[7] == "five_hour"

    def test_credits_can_never_be_the_badge(self):
        """Credits are not a rate-limit window; the badge tracks pressure."""
        assert "credits" not in icon.BADGE_WINDOWS


class TestGaugeMatchesTheBadge:
    """The panel gauge and the tray ring must read as one object.

    They diverged at first: the gauge had its own stroke ratio and no backing
    plate, which made the two rings look like different components.
    """

    def test_they_share_the_same_proportions(self):
        import inspect
        source = inspect.getsource(icon.render_gauge)
        assert "RING_STROKE_NUMBERED" in source
        assert "RING_MARGIN_NUMBERED" in source

    def test_the_gauge_has_no_geometry_of_its_own(self):
        assert not hasattr(icon, "GAUGE_STROKE")
        assert not hasattr(icon, "GAUGE_MARGIN")

    def test_the_gauge_carries_the_same_plate(self):
        """The plate is what makes the numeral legible in both surfaces."""
        from clausage_bar import theme
        gauge = icon.render_gauge(60, theme.MODERATE, False, 52)
        centre = gauge.getpixel((26, 26))
        assert centre[3] > 200, "no plate behind the numeral"

    def test_a_bare_gauge_keeps_its_hole(self):
        from clausage_bar import theme
        bare = icon.render_gauge(100, theme.CRITICAL, False, 52, numeral=False)
        assert bare.getpixel((26, 26))[3] == 0

    def test_the_arc_sweeps_the_same_way(self):
        """Both start at twelve o'clock and run clockwise, or they are not
        the same object at two sizes."""
        from clausage_bar import theme
        gauge = icon.render_gauge(25, theme.CALM, False, 64, numeral=False)
        size = gauge.size[0]
        right = sum(1 for y in range(size // 2)
                    if gauge.getpixel((size - 5, y))[3] > 200)
        left = sum(1 for y in range(size // 2)
                   if gauge.getpixel((4, y))[3] > 200)
        assert right > 0 and left == 0


class TestBadgeRepaint:
    """A paint issued before the tray is visible must be retried.

    The poll thread starts before pystray's message loop marks the icon
    visible. A badge set in that window can be dropped by the shell, and
    caching the key made it permanent: the tray kept the blank startup ring
    while the log happily reported the right number.
    """

    class FakeTray:
        def __init__(self, visible):
            self.visible = visible
            self.icon = None
            self.title = None
            self.assignments = 0

        def __setattr__(self, name, value):
            if name == "icon" and getattr(self, "icon", None) is not None:
                object.__setattr__(self, "assignments",
                                   self.assignments + 1)
            object.__setattr__(self, name, value)

    def _app(self, visible):
        from clausage_bar.app import TrayApp
        app = TrayApp.__new__(TrayApp)
        app.tray = self.FakeTray(visible)
        app._icon_key = None
        app._hover = None            # no hover card: the native tooltip path
        app._native_tip = True
        app.snapshot = None
        app.api = type("A", (), {"unavailable_reason": None,
                                 "last_error": None})()
        return app

    def test_a_paint_while_invisible_is_not_cached(self):
        app = self._app(visible=False)
        app.render()
        assert app._icon_key is None, "an unpainted badge must be retried"

    def test_a_paint_while_visible_is_cached(self):
        app = self._app(visible=True)
        app.render()
        assert app._icon_key is not None

    def test_it_repaints_on_the_next_cycle(self):
        app = self._app(visible=False)
        app.render()
        app.tray.visible = True
        before = app.tray.assignments
        app.render()
        assert app.tray.assignments > before
        assert app._icon_key is not None
