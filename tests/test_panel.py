"""Panel logic that can be tested without opening a window."""

from datetime import datetime, timedelta, timezone

import pytest

from clausage_bar import panel, theme
from clausage_bar.model import Eff

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


class TestHumanize:
    def test_hours_and_minutes(self):
        assert panel.humanize("4h24m") == "4 hr 24 min"

    def test_days_are_pluralised(self):
        assert panel.humanize("6d") == "6 days"
        assert panel.humanize("1d") == "1 day"

    def test_minutes_only(self):
        assert panel.humanize("12m") == "12 min"

    def test_seconds_only(self):
        assert panel.humanize("45s") == "45 sec"


class TestResetPhrase:
    def test_future_reset_says_when_and_how_long(self):
        eff = Eff(pct=9.0, next_reset=NOW + timedelta(hours=4, minutes=24))
        text = panel.reset_phrase(eff, NOW)
        assert text.startswith("Resets ")
        assert "in 4 hr 24 min" in text

    def test_rolled_over_window_is_explicit(self):
        eff = Eff(pct=0.0, next_reset=NOW + timedelta(hours=4), inferred=True)
        assert "reset" in panel.reset_phrase(eff, NOW).lower()

    def test_unknown_reset_is_stated(self):
        eff = Eff(pct=9.0, next_reset=None)
        assert panel.reset_phrase(eff, NOW) == "Reset time unknown"

    def test_other_day_includes_the_weekday(self):
        eff = Eff(pct=12.0, next_reset=NOW + timedelta(days=3))
        assert any(d in panel.reset_phrase(eff, NOW)
                   for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))


class TestSharedLanguage:
    """The panel must not invent its own palette or its own bands.

    It did before: three levels here, three slightly different colours in the
    widget, and a fourth set in the tray. One theme module is the fix, and
    these are the assertions that keep it that way.
    """

    def test_the_panel_owns_no_palette_of_its_own(self):
        assert not hasattr(panel, "LEVEL_HEX")
        assert not hasattr(panel, "level_of")

    def test_rows_are_labelled_from_the_shared_table(self):
        for name in panel.ROWS:
            assert name in theme.LONG_LABELS

    def test_credits_share_the_row_renderer(self):
        """Credits are a usage window like any other, visually."""
        assert "credits" in theme.LONG_LABELS


class TestPanelScaleOverride:
    """CLAUSAGE_PANEL_SCALE: one window's size, without changing the desktop.

    The panel follows Windows display scaling, which is right until it isn't:
    a 1080p laptop left at 100% where Windows would have recommended 125% gets
    a panel a third smaller than the same build on a 150% screen, and the only
    remedy was to rescale every application on the machine.
    """

    def _reloaded(self, monkeypatch, value):
        import importlib
        from clausage_bar import config
        if value is None:
            monkeypatch.delenv("CLAUSAGE_PANEL_SCALE", raising=False)
        else:
            monkeypatch.setenv("CLAUSAGE_PANEL_SCALE", value)
        return importlib.reload(config)

    def test_absent_means_no_change(self, monkeypatch):
        assert self._reloaded(monkeypatch, None).PANEL_SCALE == 1.0

    def test_it_reads_a_multiplier(self, monkeypatch):
        assert self._reloaded(monkeypatch, "1.25").PANEL_SCALE == 1.25

    @pytest.mark.parametrize("value, expected", [
        ("10", 3.0),        # a typo for 1.0 -- clamped, not obeyed
        ("0.01", 0.5),
        ("-3", 0.5),
    ])
    def test_absurd_values_are_clamped(self, monkeypatch, value, expected):
        """Unclamped, `10` yields a window bigger than the desktop.

        The panel has no title bar (it draws its own), so a window whose close
        control lands off-screen cannot be dragged back into view -- there is
        nothing to drag it by. Clamping is the difference between a bad
        setting and an unrecoverable one.
        """
        assert self._reloaded(monkeypatch, value).PANEL_SCALE == expected

    def test_junk_falls_back_rather_than_crashing(self, monkeypatch):
        assert self._reloaded(monkeypatch, "enormous").PANEL_SCALE == 1.0

    def test_it_multiplies_the_monitor_scale(self, monkeypatch):
        """The override stacks on top of DPI; it does not replace it."""
        from clausage_bar import panel as panel_mod
        monkeypatch.setattr(panel_mod, "active_monitor",
                            lambda: (1.5, (0, 0, 1920, 1032)))
        monkeypatch.setattr(panel_mod.config, "PANEL_SCALE", 1.25)
        monkeypatch.setattr(panel_mod, "declare_dpi_aware", lambda: 1.5)

        scale, _work = panel_mod.active_monitor()
        assert scale * panel_mod.config.PANEL_SCALE == pytest.approx(1.875)


class TestMultiMonitor:
    """The panel used to assume the primary display was the only one.

    Two independent bugs, which is why both halves are pinned here: the scale
    came from the *system* DPI and the position from the *primary* screen's
    dimensions -- so on a second monitor the panel was both the wrong size and
    on the wrong screen.
    """

    def test_the_query_degrades_to_single_screen_behaviour(self, monkeypatch):
        """Anything unexpected returns (1.0, None), never an exception.

        This runs before any window exists, so an exception here would take
        the panel down before it could draw a pixel.
        """
        from clausage_bar import panel as panel_mod

        class Boom:
            def __getattr__(self, _name):
                raise OSError("no user32 here")

        monkeypatch.setattr(panel_mod.ctypes, "windll", Boom())
        assert panel_mod.active_monitor() == (1.0, None)

    def test_a_real_query_returns_a_sane_scale(self):
        """On this machine, whatever it is: a plausible factor and a rect."""
        from clausage_bar import panel as panel_mod
        scale, work = panel_mod.active_monitor()
        assert 1.0 <= scale <= 4.0
        if work is not None:
            left, top, right, bottom = work
            assert right > left and bottom > top

    def test_the_work_area_excludes_the_taskbar(self):
        """Which is why the placement no longer guesses a 60px gap.

        That guess was wrong for a taskbar docked to the side, or a taller
        one; GetMonitorInfo reports the real usable rectangle.
        """
        from clausage_bar import panel as panel_mod
        import inspect
        source = inspect.getsource(panel_mod.Panel.place)
        assert "self.work_area" in source
        assert "rcWork" in inspect.getsource(panel_mod.active_monitor)

    def test_placement_uses_the_monitors_own_rectangle(self, monkeypatch):
        """A monitor at x=1920 must not place the panel at x=~1450."""
        from clausage_bar import panel as panel_mod

        seen = {}

        class FakeRoot:
            def update_idletasks(self): pass
            def winfo_reqheight(self): return 600
            def winfo_screenwidth(self): return 1920     # primary only
            def winfo_screenheight(self): return 1080
            def geometry(self, spec): seen["spec"] = spec

        inst = object.__new__(panel_mod.Panel)
        inst.root = FakeRoot()
        inst.WIDTH = 452
        inst.scale = 1.0
        # A second monitor sitting to the right of the primary.
        inst.work_area = (1920, 0, 3840, 1032)
        panel_mod.Panel.place(inst)

        import re
        match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", seen["spec"])
        assert match, seen["spec"]
        x, y = int(match.group(3)), int(match.group(4))
        assert x >= 1920, "landed on the primary monitor, not the second one"
        assert x + 452 <= 3840, "hangs off the right edge"
        assert y + 600 <= 1032, "overlaps the taskbar"
