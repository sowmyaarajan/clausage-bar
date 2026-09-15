"""The tray application: icon, menu, poll loop and notification wiring."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import pystray

from . import (autostart, card, config, freshness, hover,
               icon as icon_mod, merge, theme, tooltip)
from .atomicjson import write_atomic
from .logging_setup import get
from .model import PRIMARY_WINDOWS, Snapshot, WINDOW_LABELS
from .notifier import Notifier
from .provider_api import ApiProvider
from .provider_statusline import StatuslineProvider
from .thresholds import ThresholdTracker

log = get("app")

CREATE_NO_WINDOW = 0x08000000

_WINDOW_TITLES = {
    "five_hour": "Claude session (5h)",
    "seven_day": "Claude weekly",
}

# Both live in theme now: the panel formats money too, and two
# implementations would have drifted.
money = theme.money
join_thresholds = theme.join_thresholds


class TrayApp:
    def __init__(self, *, enable_toasts: bool = True,
                 fake: dict[str, float] | None = None) -> None:
        config.ensure_dirs()
        self.api = ApiProvider()
        self.statusline = StatuslineProvider()
        self.tracker = ThresholdTracker()
        self.notifier = Notifier(enabled=enable_toasts)
        self.fake = fake or {}

        self.snapshot: Snapshot | None = self._load_cached()
        self._api_snapshot: Snapshot | None = None
        self._icon_key: tuple | None = None
        self._hover: hover.HoverWatcher | None = None
        self._native_tip = True      # until the card proves it can appear
        self._stop = threading.Event()
        self._force = threading.Event()
        self.tray: pystray.Icon | None = None

    # ---------------------------------------------------------------- cache

    def _load_cached(self) -> Snapshot | None:
        from .atomicjson import read_json
        cached = Snapshot.from_json(read_json(config.SNAPSHOT_FILE))
        if cached is not None:
            cached.source = "cache"
            log.info("restored cached snapshot from %s", cached.captured_at)
        return cached

    def _persist(self, snapshot: Snapshot) -> None:
        write_atomic(config.SNAPSHOT_FILE, snapshot.to_json())

    # ---------------------------------------------------------------- state

    def _apply_fake(self, snapshot: Snapshot | None) -> Snapshot | None:
        """--fake-pct support, for verifying notifications without burning quota."""
        if not self.fake or snapshot is None:
            return snapshot
        from .model import WindowUsage
        for name, pct in self.fake.items():
            existing = snapshot.windows.get(name)
            snapshot.windows[name] = WindowUsage(
                utilization=pct,
                resets_at=existing.resets_at if existing else None,
                raw_key_path="fake")
        return snapshot

    def refresh_data(self) -> None:
        """Poll whichever sources are due and merge the result."""
        if self.api.due():
            polled = self.api.poll()
            if polled is not None:
                self._api_snapshot = polled

        statusline = self.statusline.read()
        chosen = merge.choose(self._api_snapshot, statusline, self.snapshot)
        if chosen is not None:
            chosen = self._apply_fake(chosen)
            self.snapshot = chosen
            if chosen.source != "cache":
                self._persist(chosen)

    def evaluate(self, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        effs, health, age = freshness.evaluate(self.snapshot, now)

        note = self.api.unavailable_reason or self.api.last_error
        session = effs.get("five_hour")
        weekly = effs.get("seven_day")
        session_pct = session.pct if session else None
        weekly_pct = weekly.pct if weekly else None
        if health == freshness.UNKNOWN:
            session_pct = weekly_pct = None
        return effs, health, age, (session_pct, weekly_pct), note

    # ---------------------------------------------------------------- render

    def render(self) -> None:
        if self.tray is None:
            return
        effs, health, age, (session_pct, weekly_pct), note = self.evaluate()
        dimmed = health == freshness.STALE
        image, key = icon_mod.render_state(session_pct, weekly_pct,
                                           dimmed=dimmed)
        # Only touch the icon when it actually changed: needless
        # Shell_NotifyIcon churn is what makes tray apps flicker.
        if key != self._icon_key:
            self.tray.icon = image
            # Remember the key only if the icon was actually live when we set
            # it. The poll thread starts before pystray's message loop marks
            # the icon visible, and a paint issued in that window can be
            # dropped -- caching the key would make that permanent, leaving
            # the badge on its blank startup image until the percentage
            # happened to change. Observed in the wild: a bare ring in the
            # tray while the log said "badge -> '88'".
            visible = bool(getattr(self.tray, "visible", False))
            self._icon_key = key if visible else None
            # Logged because "the badge is not updating" is otherwise
            # indistinguishable from "the badge has nothing to show".
            log.info("badge -> %r (%s) visible=%s%s", key[0], key[1], visible,
                     "" if visible else "  (will repaint)")
        # The native tooltip is a 127-character plain-text field and can only
        # ever look like console output, so the hover card replaces it -- but
        # only once the card has proven it can find the icon and appear.
        # Otherwise the app would trade a working tooltip for nothing.
        if self._hover is not None and self._hover.available():
            if self._native_tip:
                log.info("hover card is live; native tooltip suppressed")
                self._native_tip = False
            # Keep a short title anyway: screen readers announce it, and it is
            # what shows if the shell decides to draw a tooltip regardless.
            self.tray.title = "Claude Usage"
        else:
            self.tray.title = tooltip.compose(self.snapshot, effs, health,
                                              age, note)

    # ---------------------------------------------------------------- notify

    def check_thresholds(self) -> None:
        effs, health, age, _pcts, _note = self.evaluate()
        if health == freshness.UNKNOWN:
            return
        for name in PRIMARY_WINDOWS:
            eff = effs.get(name)
            if eff is None:
                continue
            crossed = self.tracker.evaluate(name, eff, age)
            if crossed:
                self._toast(name, eff, crossed, effs)

        spend = self.snapshot.spend if self.snapshot else None
        crossed = self.tracker.evaluate_credits(spend, age)
        if crossed:
            self._toast_credits(spend, crossed, effs)

        self.tracker.finish_cycle()

    def _toast(self, name: str, eff, crossed: list[int], effs: dict) -> None:
        highest = max(crossed)
        title = "{0} at {1:.0f}%".format(
            _WINDOW_TITLES.get(name, name), eff.pct)

        parts = []
        if len(crossed) > 1:
            parts.append("Passed {0}.".format(join_thresholds(crossed)))
        remaining = freshness.fmt_until(eff.next_reset)
        if eff.next_reset is not None:
            local = eff.next_reset.astimezone().strftime("%H:%M")
            parts.append("Resets {0}{1}.".format(
                local, " (in {0})".format(remaining) if remaining else ""))

        # Always name the other window: that context is the whole point of not
        # having to open Claude.
        other = "seven_day" if name == "five_hour" else "five_hour"
        other_eff = effs.get(other)
        if other_eff is not None and other_eff.pct is not None:
            parts.append("{0} at {1:.0f}%.".format(
                "Weekly" if other == "seven_day" else "Session",
                other_eff.pct))
        if self.snapshot and self.snapshot.spend and self.snapshot.spend.enabled:
            parts.append("Credits at {0:.0f}%.".format(self.snapshot.spend.percent))

        self.notifier.notify(title, " ".join(parts))

    def _toast_credits(self, spend, crossed: list[int], effs: dict) -> None:
        """Credits are the only number here in real money, so say the money."""
        highest = max(crossed)
        title = "Claude usage credits at {0:.0f}%".format(spend.percent)

        parts = []
        if len(crossed) > 1:
            parts.append("Passed {0}.".format(join_thresholds(crossed)))
        if spend.used_amount is not None and spend.limit_amount is not None:
            parts.append("{0} of {1} used.".format(
                money(spend.used_amount, spend.currency),
                money(spend.limit_amount, spend.currency)))
        if highest >= 100 or spend.limit_reached:
            parts.append("The cap is reached: expect to be blocked when a "
                         "rate-limit window runs out.")
        else:
            # The one thing worth repeating: this pool is not consumed by
            # ordinary use, only by work that continues past a spent window.
            parts.append("Credits are only drawn after a session or weekly "
                         "limit is exhausted.")

        session = effs.get("five_hour")
        weekly = effs.get("seven_day")
        if session is not None and session.pct is not None                 and weekly is not None and weekly.pct is not None:
            parts.append("Session {0:.0f}%, weekly {1:.0f}%.".format(
                session.pct, weekly.pct))

        self.notifier.notify(title, " ".join(parts))

    # ---------------------------------------------------------------- loop

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._consume_refresh_request()
                self.refresh_data()
                self.check_thresholds()
                self.render()
            except Exception:
                log.exception("poll cycle failed")
            # Re-evaluate on a short tick so the icon dims and windows roll
            # over on time even when no source produced anything new.
            if self._force.wait(timeout=config.TICK_S):
                self._force.clear()

    def _consume_refresh_request(self) -> None:
        """The panel drops a file here to ask for an immediate poll."""
        request = config.CLAUSAGE_DIR / "refresh.request"
        try:
            if not request.exists():
                return
            request.unlink(missing_ok=True)
        except OSError:
            return
        log.info("refresh requested from the panel")
        self.api.force_due()

    # ---------------------------------------------------------------- menu

    def _on_refresh(self) -> None:
        log.info("manual refresh requested")
        self.api.force_due()
        self._force.set()

    def _spawn(self, module: str, label: str) -> bool:
        """Launch a Tk surface as its own process.

        Tk insists every call happen on the thread that created the
        interpreter, and pystray does not guarantee which thread runs a menu
        callback. A separate process avoids that entirely, and a crash there
        cannot take the tray down with it.
        """
        if config.FROZEN:
            # One executable, asked to come up as a different surface. It
            # cannot take `-m`: sys.executable is the app, not an interpreter.
            command = [sys.executable, "--" + module.rsplit(".", 1)[-1]]
            cwd = None
        else:
            interpreter = Path(sys.executable)
            windowless = interpreter.with_name("pythonw.exe")
            exe = windowless if windowless.exists() else interpreter
            command = [str(exe), "-m", module]
            cwd = str(Path(__file__).resolve().parents[1])
        try:
            subprocess.Popen(command, cwd=cwd,
                             creationflags=CREATE_NO_WINDOW)
            return True
        except OSError as exc:
            log.warning("could not open %s: %s", label, exc)
            self.notifier.notify("clausage_bar",
                                 "Could not open the {0}.".format(label))
            return False

    def _hover_image(self):
        """The card image for the current reading, for the hover watcher.

        Rendered on demand rather than cached: the card shows a live countdown
        ("resets in 2h 33m") and a stale card is the whole thing this is meant
        to avoid.
        """
        try:
            effs, health, age, _pcts, note = self.evaluate()
            return card.render(self.snapshot, effs, health, age, note)
        except Exception:
            log.exception("could not render the hover card")
            return None

    def _start_hover(self) -> None:
        """Start watching for hover.

        The handle is passed as a callable because pystray has not created its
        message window yet at this point -- it does that inside its own
        mainloop thread. Looking it up eagerly here found nothing and left the
        card permanently disabled.
        """
        self._hover = hover.HoverWatcher(
            lambda: hover.tray_hwnd(self.tray), self._hover_image)
        self._hover.start()

    def _on_panel(self) -> None:
        self._spawn("clausage_bar.panel", "usage panel")

    def _on_widget(self) -> None:
        """The big floating indicator, for when 16px in the tray is too small."""
        self._spawn("clausage_bar.widget", "taskbar widget")

    def _on_copy_details(self) -> None:
        effs, health, age, _pcts, note = self.evaluate()
        text = tooltip.details(self.snapshot, effs, health, age, note)
        if self._copy(text):
            self.notifier.notify("clausage_bar", "Details copied to clipboard.")

    @staticmethod
    def _copy(text: str) -> bool:
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
            return True
        except Exception as exc:
            log.warning("clipboard copy failed: %s", exc)
            return False

    def _on_open_usage(self) -> None:
        webbrowser.open(config.USAGE_PAGE_URL)

    def _on_reset_notifications(self) -> None:
        self.tracker.reset()
        self.notifier.notify("clausage_bar",
                             "Notification thresholds re-armed for this period.")

    def _on_toggle_autostart(self) -> None:
        autostart.toggle()

    def _on_quit(self) -> None:
        log.info("quit requested")
        self._stop.set()
        self._force.set()
        if self.tray is not None:
            self.tray.stop()

    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item("Show usage panel", lambda: self._on_panel(), default=True),
            item("Show big taskbar widget", lambda: self._on_widget()),
            item("Refresh now", lambda: self._on_refresh()),
            item("Copy details", lambda: self._on_copy_details()),
            pystray.Menu.SEPARATOR,
            item("Open usage page", lambda: self._on_open_usage()),
            pystray.Menu.SEPARATOR,
            item("Reset notification state", lambda: self._on_reset_notifications()),
            item("Start with Windows", lambda: self._on_toggle_autostart(),
                 checked=lambda _: autostart.is_enabled()),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda: self._on_quit()),
        )

    # ---------------------------------------------------------------- run

    def run(self) -> int:
        image, self._icon_key = icon_mod.render_state(None, None)
        self.tray = pystray.Icon(config.APP_ID, image, "Claude usage — starting…",
                                 menu=self._menu())
        worker = threading.Thread(target=self._loop, name="clausage-poll",
                                  daemon=True)
        worker.start()
        if config.HOVER_CARD:
            self._start_hover()
        if config.SHOW_WIDGET:
            self._on_widget()
        log.info("tray started")
        try:
            self.tray.run()
        finally:
            self._stop.set()
            self._force.set()
            if self._hover is not None:
                self._hover.stop()
        log.info("tray stopped")
        return 0

    # ---------------------------------------------------------------- once

    def run_once(self, diagnose: bool = False) -> int:
        """Single cycle, no tray. Used by --once/--diagnose."""
        self.refresh_data()
        effs, health, age, (session_pct, weekly_pct), note = self.evaluate()

        if diagnose:
            print(tooltip.details(self.snapshot, effs, health, age, note))
            print()
            _image, badge_key = icon_mod.render_state(
                session_pct, weekly_pct, dimmed=health == freshness.STALE)
            if icon_mod.STYLE not in icon_mod._DIGIT_STYLES:
                shown = badge_key[7]
                print("badge:    {0}% ({1})  dimmed={2}  size={3}px".format(
                    badge_key[0], badge_key[1], badge_key[4], badge_key[5]))
                print("          style={0}  tint={1}  shows {2}".format(
                    icon_mod.STYLE,
                    theme.hex_of(theme.plate_tint(badge_key[1], badge_key[4]))
                    if icon_mod.STYLE == "number"
                    else theme.accent_hex(badge_key[1], badge_key[4]),
                    theme.LONG_LABELS.get(shown, shown)))
            elif icon_mod.ICON_ROWS == 1:
                print("badge:    {0!r} ({1})  dimmed={2}  size={3}px  "
                      "[1 row: the higher window]".format(
                          badge_key[0], badge_key[1], badge_key[4],
                          badge_key[5]))
                print("          style={0}  font={1}  ink={2}".format(
                    icon_mod.STYLE, icon_mod.FONT_FAMILY, icon_mod.INK_MODE))
            else:
                print("badge:    top={0!r} ({1})  bottom={2!r} ({3})  "
                      "dimmed={4}  size={5}px".format(*badge_key[:6]))
                print("          style={0}  font={1}  ink={2}".format(
                    icon_mod.STYLE, icon_mod.FONT_FAMILY, icon_mod.INK_MODE))
            tip = tooltip.compose(self.snapshot, effs, health, age, note)
            print("tooltip:")
            for line in tip.split("\n"):
                print("          | " + line)
            print("          ({0} chars, limit {1})".format(
                len(tip), config.TOOLTIP_MAX))
            print("api:      interval={0}s last_error={1!r} unavailable={2!r}".format(
                self.api.interval_s(), self.api.last_error,
                self.api.unavailable_reason))
            sl = self.statusline.read()
            print("statusline: {0}".format(
                "no data at {0}".format(config.STATE_FILE) if sl is None
                else "captured {0} ago".format(freshness.fmt_ago(sl.age_s()))))

        self.check_thresholds()
        return 0 if self.snapshot is not None else 1
