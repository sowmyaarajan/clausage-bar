"""The hover machinery, minus Windows.

Placement, premultiplication and the fallback rule are pure logic and worth
pinning down. Creating a real layered window is not something a test suite
should do, so the window itself is exercised by the manual hover capture in
the project notes instead.
"""

import ctypes

import pytest

from clausage_bar import hover
from PIL import Image


class TestIconIdentity:
    def test_a_missing_window_is_not_an_error(self):
        """The tray window does not exist until pystray's loop creates it."""
        assert hover.icon_rect(None) is None
        assert hover.icon_rect(0) is None

    def test_a_bogus_window_returns_no_rect(self):
        assert hover.icon_rect(0xDEAD) is None

    def test_the_identifier_struct_matches_the_shell_abi(self):
        """cbSize is validated by the shell; a wrong layout silently fails.

        Asserting the byte count outright was a guess and a wrong one (it is
        40 on x64, not 32). The properties that actually matter are that hWnd
        is pointer-aligned after cbSize and that the struct pads to a pointer
        boundary -- get either wrong and the shell rejects the call.
        """
        offsets = {name: getattr(hover.NOTIFYICONIDENTIFIER, name).offset
                   for name, _t in hover.NOTIFYICONIDENTIFIER._fields_}
        pointer = ctypes.sizeof(ctypes.c_void_p)
        assert offsets["cbSize"] == 0
        assert offsets["hWnd"] == pointer
        assert offsets["uID"] == pointer * 2
        assert ctypes.sizeof(hover.NOTIFYICONIDENTIFIER) % pointer == 0

    def test_the_blend_struct_is_four_bytes(self):
        assert ctypes.sizeof(hover.BLENDFUNCTION) == 4

    def test_premultiplied_alpha_is_declared(self):
        """AC_SRC_ALPHA means premultiplied; the wrong value fringes glyphs."""
        assert hover.AC_SRC_ALPHA == 1
        assert hover.ULW_ALPHA == 2


class TestPremultiply:
    def test_opaque_pixels_survive_unchanged(self):
        image = Image.new("RGBA", (1, 1), (10, 20, 30, 255))
        assert hover._premultiplied_bgra(image) == bytes((30, 20, 10, 255))

    def test_transparent_pixels_go_black(self):
        """Premultiplied means colour times alpha; alpha 0 erases the colour."""
        image = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
        assert hover._premultiplied_bgra(image) == bytes((0, 0, 0, 0))

    def test_half_alpha_halves_the_colour(self):
        image = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
        blue, green, red, alpha = hover._premultiplied_bgra(image)
        assert alpha == 128
        assert 98 <= red <= 101          # 200 * 128/255
        assert 49 <= green <= 51
        assert 24 <= blue <= 26

    def test_channel_order_is_bgra(self):
        image = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
        assert hover._premultiplied_bgra(image)[:3] == bytes((0, 0, 255))


class FakeWatcher(hover.HoverWatcher):
    def __init__(self):
        super().__init__(lambda: None, lambda: None)


class TestPlacement:
    @pytest.fixture
    def watcher(self):
        return FakeWatcher()

    def test_it_sits_above_a_bottom_taskbar(self, watcher, monkeypatch):
        monkeypatch.setattr(hover, "work_area", lambda: (0, 0, 1920, 1032))
        icon = (1449, 1032, 1497, 1080)          # a 48px icon in the taskbar
        x, y = watcher.place(icon, (308, 283))
        assert y + 283 <= 1032, "the card must clear the taskbar"

    def test_it_is_centred_on_the_icon(self, watcher, monkeypatch):
        monkeypatch.setattr(hover, "work_area", lambda: (0, 0, 1920, 1032))
        x, _y = watcher.place((1000, 1032, 1048, 1080), (300, 200))
        assert abs((x + 150) - 1024) <= 1

    def test_it_flips_below_a_top_taskbar(self, watcher, monkeypatch):
        """No room above means the card goes under the icon, not off-screen."""
        monkeypatch.setattr(hover, "work_area", lambda: (0, 48, 1920, 1080))
        _x, y = watcher.place((900, 0, 948, 48), (308, 283))
        assert y >= 48

    def test_it_stays_inside_the_work_area(self, watcher, monkeypatch):
        monkeypatch.setattr(hover, "work_area", lambda: (0, 0, 1920, 1032))
        x, y = watcher.place((1900, 1032, 1920, 1080), (308, 283))
        assert 0 <= x and x + 308 <= 1920
        assert 0 <= y and y + 283 <= 1032

    def test_a_narrow_screen_still_places_it(self, watcher, monkeypatch):
        monkeypatch.setattr(hover, "work_area", lambda: (0, 0, 300, 400))
        x, y = watcher.place((280, 400, 300, 420), (308, 283))
        assert isinstance(x, int) and isinstance(y, int)


class TestFallback:
    def test_a_fresh_watcher_is_not_available(self):
        """The native tooltip must not be dropped before the card can appear."""
        assert FakeWatcher().available() is False

    def test_it_becomes_available_once_the_icon_is_located(self):
        watcher = FakeWatcher()
        watcher._located = True
        assert watcher.available() is True

    def test_a_failure_permanently_gives_up(self):
        """One exception and the native tooltip comes back for good."""
        watcher = FakeWatcher()
        watcher._located = True
        watcher._failed = True
        assert watcher.available() is False

    def test_there_is_grace_before_hiding(self):
        """The cursor crosses the icon's edge constantly on the way past."""
        assert hover.HoverWatcher.GRACE >= 2

    def test_the_poll_is_fast_enough_to_feel_instant(self):
        assert hover.HoverWatcher.POLL_S <= 0.15


class TestClickThrough:
    def test_the_window_never_takes_input_or_focus(self):
        """A card that swallowed a click on the icon would be a regression."""
        import inspect
        source = inspect.getsource(hover.LayeredCard.create)
        for flag in ("WS_EX_TRANSPARENT", "WS_EX_NOACTIVATE",
                     "WS_EX_TOOLWINDOW", "WS_EX_LAYERED"):
            assert flag in source, flag

    def test_the_window_procedure_is_kept_alive(self):
        """ctypes does not hold the callback; a collected WNDPROC crashes."""
        import inspect
        assert "self._proc" in inspect.getsource(hover.LayeredCard.create)
