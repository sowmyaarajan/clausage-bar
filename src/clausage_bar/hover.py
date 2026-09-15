"""Putting the hover card on screen.

``card.py`` draws the image; this module owns the Windows side of showing it
when the cursor rests on the tray icon.

Three problems had to be solved, none of them obvious:

*Finding the icon.* A card has to appear next to the icon, so we need the
icon's rectangle on screen. ``Shell_NotifyIconGetRect`` gives it, but it wants
the icon's identity -- the owning window and its ``uID``. pystray never
exposes either. Its ``_message()`` builds a ``NOTIFYICONDATAW`` with
``hID=id(self)``, and there is no ``hID`` field: ctypes accepts the unknown
keyword as an ordinary Python attribute and drops it, so **the icon's real
uID is 0**. Verified by constructing the struct and reading ``uID`` back.
That is a latent bug in pystray, but a stable one, and it is what makes this
work at all.

*Drawing something that is not a rectangle.* A normal window cannot have
rounded corners or a shadow. A layered window can: ``UpdateLayeredWindow``
takes a 32-bit bitmap with per-pixel alpha and composites it against whatever
is behind. The alpha has to be premultiplied, which is what ``AC_SRC_ALPHA``
means, and getting that wrong shows up as pale fringing rather than an error.

*Not stealing input.* The card must never take focus or swallow a click aimed
at the icon underneath it, so it is ``WS_EX_TRANSPARENT`` (hit-tests pass
straight through), ``WS_EX_NOACTIVATE`` and ``WS_EX_TOOLWINDOW`` (no taskbar
button, no alt-tab entry).

If any of this fails the app must not lose its tooltip, so ``available()``
reports whether the icon could be located, and ``app.py`` only suppresses the
native text tooltip once the card has proven it can find the icon.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from . import theme
from .logging_setup import get

log = get("hover")

# ---------------------------------------------------------------- win32

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x02
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
SPI_GETWORKAREA = 0x0030

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             ctypes.c_longlong, ctypes.c_longlong)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.ULONG), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", GUID)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


def work_area() -> tuple[int, int, int, int]:
    """The desktop minus the taskbar, so the card is never drawn under it."""
    rect = wintypes.RECT()
    try:
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0,
                                        ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except OSError:
        pass
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def icon_rect(hwnd, uid: int = 0) -> tuple[int, int, int, int] | None:
    """The tray icon's rectangle on screen, or None if it cannot be found.

    Returns None when the icon lives in the overflow flyout and that flyout is
    closed -- there is genuinely no on-screen rectangle then, and the caller
    has to fall back to the cursor position.
    """
    if not hwnd:
        return None
    ident = NOTIFYICONIDENTIFIER()
    ident.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
    ident.hWnd = wintypes.HWND(hwnd)
    ident.uID = uid
    rect = wintypes.RECT()
    try:
        result = shell32.Shell_NotifyIconGetRect(ctypes.byref(ident),
                                                 ctypes.byref(rect))
    except (AttributeError, OSError):
        return None
    if result != 0:                     # S_OK is 0; S_FALSE means "hidden"
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _premultiplied_bgra(image) -> bytes:
    """BGRA bytes with colour premultiplied by alpha, top-down.

    AC_SRC_ALPHA means premultiplied. Handing it straight RGBA produces a
    card with a pale halo around every glyph -- no error, just a subtly wrong
    picture, which is the worst kind of bug to chase.
    """
    from PIL import Image, ImageChops
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    red, green, blue, alpha = image.split()
    red = ImageChops.multiply(red, alpha)
    green = ImageChops.multiply(green, alpha)
    blue = ImageChops.multiply(blue, alpha)
    return Image.merge("RGBA", (blue, green, red, alpha)).tobytes()


class LayeredCard:
    """A borderless, click-through, per-pixel-alpha window."""

    CLASS_NAME = "ClausageHoverCard"

    def __init__(self) -> None:
        self._hwnd = None
        self._atom = None
        self._proc = None
        self._shown = False

    def create(self) -> bool:
        if self._hwnd:
            return True
        try:
            # Kept on the instance: ctypes does not hold a reference to the
            # callback, and a garbage collected WNDPROC crashes the process
            # the next time Windows dispatches to it.
            self._proc = WNDPROC(
                lambda hwnd, msg, wparam, lparam:
                user32.DefWindowProcW(hwnd, msg, wparam, lparam))

            cls = WNDCLASSEX()
            cls.cbSize = ctypes.sizeof(WNDCLASSEX)
            cls.lpfnWndProc = self._proc
            cls.hInstance = wintypes.HINSTANCE(
                ctypes.windll.kernel32.GetModuleHandleW(None))
            cls.lpszClassName = self.CLASS_NAME
            self._atom = user32.RegisterClassExW(ctypes.byref(cls))

            self._hwnd = user32.CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
                | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
                self.CLASS_NAME if self._atom else None,
                "Claude usage", WS_POPUP, 0, 0, 10, 10,
                None, None, cls.hInstance, None)
            return bool(self._hwnd)
        except Exception:
            log.exception("could not create the hover window")
            self._hwnd = None
            return False

    def show(self, image, x: int, y: int) -> bool:
        if not self.create():
            return False
        width, height = image.size
        data = _premultiplied_bgra(image)

        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = old = None
        try:
            header = BITMAPINFOHEADER()
            header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            header.biWidth = width
            header.biHeight = -height        # negative: top-down rows
            header.biPlanes = 1
            header.biBitCount = 32
            header.biCompression = BI_RGB

            bits = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(
                mem_dc, ctypes.byref(header), DIB_RGB_COLORS,
                ctypes.byref(bits), None, 0)
            if not bitmap:
                return False
            ctypes.memmove(bits, data, len(data))
            old = gdi32.SelectObject(mem_dc, bitmap)

            src = wintypes.POINT(0, 0)
            dst = wintypes.POINT(int(x), int(y))
            size = wintypes.SIZE(width, height)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

            ok = user32.UpdateLayeredWindow(
                self._hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(size),
                mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
            if ok and not self._shown:
                user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
                self._shown = True
            return bool(ok)
        finally:
            if old:
                gdi32.SelectObject(mem_dc, old)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)

    def hide(self) -> None:
        if self._hwnd and self._shown:
            user32.ShowWindow(self._hwnd, SW_HIDE)
            self._shown = False

    def destroy(self) -> None:
        self.hide()
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None

    @property
    def visible(self) -> bool:
        return self._shown


class HoverWatcher(threading.Thread):
    """Shows the card while the cursor rests on the tray icon.

    Polling rather than events, because the icon's mouse messages go to
    pystray's own window and it does not forward them. 90ms is under the
    threshold where a tooltip feels laggy and costs one GetCursorPos plus one
    rect lookup, both trivial.
    """

    POLL_S = 0.09
    GRACE = 3            # polls the cursor may be away before hiding

    def __init__(self, hwnd_provider, image_provider, uid: int = 0) -> None:
        super().__init__(name="clausage-hover", daemon=True)
        # A provider rather than a handle: pystray creates its message window
        # inside its own mainloop thread, so at the moment the tray is asked
        # to run there is no hwnd yet. Resolving it on every tick also means a
        # window recreated later is picked up for free.
        self._hwnd_provider = hwnd_provider
        self._provider = image_provider
        self._uid = uid
        self._stop = threading.Event()
        self._card = LayeredCard()
        self._away = 0
        self._located = False
        self._failed = False

    # ------------------------------------------------------------ geometry

    def available(self) -> bool:
        """Whether the icon could be located at least once.

        app.py uses this to decide whether to suppress the native tooltip:
        replacing a working text tooltip with a card that can never appear
        would be strictly worse than leaving it alone.
        """
        return self._located and not self._failed

    def place(self, rect, size) -> tuple[int, int]:
        """Above the icon, right-aligned to it, clamped to the work area."""
        left, top, right, bottom = rect
        width, height = size
        wa_left, wa_top, wa_right, wa_bottom = work_area()

        x = (left + right) // 2 - width // 2
        y = top - height - 6
        if y < wa_top:                      # taskbar at the top
            y = bottom + 6
        x = max(wa_left + 4, min(x, wa_right - width - 4))
        y = max(wa_top + 4, min(y, wa_bottom - height - 4))
        return x, y

    # ------------------------------------------------------------ loop

    def run(self) -> None:
        message = wintypes.MSG()
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                if not self._failed:
                    log.exception("hover card failed; falling back to the "
                                  "native tooltip")
                self._failed = True
                try:
                    self._card.destroy()
                except Exception:
                    pass
                return
            # The window needs its queue drained or Windows considers the
            # thread unresponsive, even though nothing is ever sent to it.
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            self._stop.wait(self.POLL_S)
        self._card.destroy()

    def _tick(self) -> None:
        hwnd = self._hwnd_provider()
        if not hwnd:
            return                  # the tray window does not exist yet
        rect = icon_rect(hwnd, self._uid)
        if rect is None:
            # The icon has no rectangle right now (hidden in the overflow).
            # Nothing to hover, so make sure the card is not left behind.
            if self._card.visible:
                self._card.hide()
            return
        self._located = True

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        inside = (rect[0] <= point.x < rect[2] and rect[1] <= point.y < rect[3])

        if inside:
            self._away = 0
            image = self._provider()
            if image is None:
                return
            x, y = self.place(rect, image.size)
            self._card.show(image, x, y)
        elif self._card.visible:
            # A few polls of grace: the cursor crosses the icon's edge
            # constantly on the way past, and a card that vanishes on the
            # first pixel of travel flickers.
            self._away += 1
            if self._away >= self.GRACE:
                self._card.hide()
                self._away = 0

    def stop(self) -> None:
        self._stop.set()


def tray_hwnd(tray) -> int | None:
    """pystray's own message window, which owns the notify icon."""
    return getattr(tray, "_hwnd", None)
