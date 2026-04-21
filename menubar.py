"""Slouchy — macOS Menubar App.

Lives in the system tray. Shows real-time posture status, streak timer,
daily stats, and escalation controls via a native dropdown menu.
Backend (posture detection, escalation, audio) runs on daemon threads.
"""

import os
import subprocess
import sys
import threading
import time
import plistlib
import tempfile

import rumps

from audio import AudioPlayer
from config import (
    CALIBRATION_FILE,
    LANDMARK_CONFIDENCE_MIN,
    POLL_INTERVAL_SECONDS,
    SESSION_BREAK_HOURS,
)
from escalation import EscalationEngine
from posture import PostureDetector, SharedState
from tracker import PostureTracker
from dashboard import render_dashboard_html


# ── Status icons ──────────────────────────────────────────────────────
ICON_GOOD = "[OK]"
ICON_SLOUCHING = "[!]"
ICON_SCOLDING = "[X]"
ICON_OFFLINE = "[-]"
ICON_PAUSED = "[||]"


def _format_minutes(seconds: float) -> str:
    """Format seconds as a human-readable duration string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining = minutes % 60
    return f"{hours}h {remaining}m"


def _notify(title: str, message: str) -> None:
    """Send a macOS notification."""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.nidhi.slouchy.plist")


def set_autostart(enabled: bool):
    """Create or remove a LaunchAgent plist to run on boot."""
    if enabled:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        venv_python = os.path.join(repo_dir, "venv", "bin", "python")
        script = os.path.join(repo_dir, "menubar.py")

        plist_data = {
            "Label": "com.nidhi.slouchy",
            "ProgramArguments": [venv_python, script],
            "RunAtLoad": True,
            "WorkingDirectory": repo_dir,
            "StandardOutPath": os.path.expanduser("~/.slouchy/slouchy.log"),
            "StandardErrorPath": os.path.expanduser("~/.slouchy/slouchy_err.log"),
        }

        os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
        with open(PLIST_PATH, "wb") as f:
            plistlib.dump(plist_data, f)
    else:
        if os.path.exists(PLIST_PATH):
            os.remove(PLIST_PATH)


def _resolve_menu_icon_path() -> str:
    """Prefer dedicated menubar template icon; fallback to app icon."""
    if os.environ.get("RESOURCEPATH"):
        base = os.environ.get("RESOURCEPATH")
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    preferred = os.path.join(base, "menubar_icon.png")
    fallback = os.path.join(base, "icon.png")
    return preferred if os.path.exists(preferred) else fallback


MENU_ICON_PATH = _resolve_menu_icon_path()


class SlouchyApp(rumps.App):
    """macOS menubar app for posture monitoring."""

    def __init__(self):
        super().__init__(
            name="Slouchy",
            title="",  # Title stays empty unless monitoring stats override it
            icon=MENU_ICON_PATH,
            quit_button=None,  # we add our own
        )
        # Render as a native template icon so it follows macOS menubar style.
        self.template = True

        # ── Backend ───────────────────────────────────────────────────
        self.shared_state = SharedState()
        self.detector = PostureDetector(
            self.shared_state, poll_interval=POLL_INTERVAL_SECONDS
        )
        self.engine = EscalationEngine()
        self.audio = AudioPlayer()
        self.tracker = PostureTracker()

        # ── State ─────────────────────────────────────────────────────
        self._monitoring = False
        self._detector_thread: threading.Thread | None = None
        self._session_id: int | None = None
        self._session_start: float | None = None
        self._last_logged_time: float | None = None
        self._streak_start: float | None = None
        self._pause_until: float | None = None
        self._absence_id: int | None = None

        # ── Menu items ────────────────────────────────────────────────
        self.status_item = rumps.MenuItem("Not monitoring", callback=None)
        self.status_item.set_callback(None)

        self.streak_item = rumps.MenuItem("Streak: --")
        self.streak_item.set_callback(None)

        self.stats_item = rumps.MenuItem("Today: --")
        self.stats_item.set_callback(None)

        # Pause/Resume submenu
        self.monitoring_menu = rumps.MenuItem("Monitoring")
        self.resume_item = rumps.MenuItem("Resume", callback=self._resume_monitoring)
        self.pause_30m_item = rumps.MenuItem(
            "Pause for 30 min",
            callback=lambda sender: self._pause_monitoring(30, sender),
        )
        self.pause_1h_item = rumps.MenuItem(
            "Pause for 1 hour",
            callback=lambda sender: self._pause_monitoring(60, sender),
        )
        self.pause_tomorrow_item = rumps.MenuItem(
            "Pause until tomorrow",
            callback=lambda sender: self._pause_monitoring(-1, sender),
        )

        self.monitoring_menu.add(self.resume_item)
        self.monitoring_menu.add(self.pause_30m_item)
        self.monitoring_menu.add(self.pause_1h_item)
        self.monitoring_menu.add(self.pause_tomorrow_item)

        self.calibrate_item = rumps.MenuItem("Recalibrate", callback=self._recalibrate)
        self.dashboard_item = rumps.MenuItem("Open Dashboard", callback=self._open_dashboard)

        # Camera submenu
        self.camera_items = {}
        camera_menu = rumps.MenuItem("Camera")
        for i in range(4):
            label = (
                f"{'● ' if i == 0 else '○ '}Camera {i}{' (Default)' if i == 0 else ''}"
            )
            item = rumps.MenuItem(
                label, callback=lambda sender, idx=i: self._set_camera(idx, sender)
            )
            self.camera_items[i] = item
            camera_menu.add(item)

        quit_item = rumps.MenuItem("Quit Slouchy", callback=self._quit)

        self.menu = [
            self.status_item,
            None,  # separator
            self.streak_item,
            self.stats_item,
            None,
            self.monitoring_menu,
            self.calibrate_item,
            self.dashboard_item,
            None,
            camera_menu,
            None,
            quit_item,
        ]

        # ── Poll timer ────────────────────────────────────────────────
        # Keep this timer always running once initialized. Recalibration runs
        # in a worker thread, and restarting rumps timers from that thread can
        # leave UI updates stuck on "Monitoring…".
        self._poll_timer = rumps.Timer(self._poll_callback, POLL_INTERVAL_SECONDS)
        self._poll_timer.start()

        # ── Stats refresh timer (every 30s) ───────────────────────────
        self._stats_timer = rumps.Timer(self._refresh_stats, 30)
        self._stats_timer.start()
        self._refresh_monitoring_menu()

        _notify("Slouchy", "Sit tall. Slouchy is watching.")

        # ── First-run onboarding or auto-start ────────────────────────
        if self.shared_state.is_calibrated:
            self._start_monitoring()
        else:
            self._first_run_onboarding()

    # ══════════════════════════════════════════════════════════════════
    # Monitoring lifecycle
    # ══════════════════════════════════════════════════════════════════

    def _refresh_monitoring_menu(self) -> None:
        """Keep monitoring submenu labels/actions in sync with app state."""
        if self._monitoring and self._pause_until is None:
            self.monitoring_menu.title = "Monitoring (On)"
            self.resume_item.title = "Resume (Already On)"
            self.resume_item.set_callback(None)
        elif self._pause_until is not None:
            self.monitoring_menu.title = "Monitoring (Paused)"
            self.resume_item.title = "Resume"
            self.resume_item.set_callback(self._resume_monitoring)
        else:
            self.monitoring_menu.title = "Monitoring (Off)"
            self.resume_item.title = "Resume"
            self.resume_item.set_callback(self._resume_monitoring)

    def _first_run_onboarding(self):
        """Welcome flow for first-time users. Auto-starts calibration."""
        self.title = " Setup..."
        self.status_item.title = "Welcome! Setting up..."

        def _onboard():
            # Show welcome notification
            _notify(
                "Welcome to Slouchy!",
                "Sit up straight — calibrating your posture in 3 seconds...",
            )
            time.sleep(3)

            # Auto-calibrate
            self.status_item.title = "Calibrating — hold still!"
            self.title = " Calibrating..."
            try:
                self.detector.calibrate()
                _notify("Slouchy", "✓ Calibration done! Monitoring your posture now.")
                self._start_monitoring()
            except Exception as e:
                _notify(
                    "Slouchy", f"Setup failed: {e}. Click Recalibrate to try again."
                )
                self.title = " Error"
                self.status_item.title = "Click Recalibrate to set up"

        threading.Thread(target=_onboard, daemon=True).start()

    def _start_monitoring(self):
        """Start posture detection and mark monitoring as active."""
        if self._monitoring:
            return

        self._monitoring = True
        self._pause_until = None

        SlouchyApp._start_tracking_session(self)
        self._last_logged_time = None
        self._streak_start = time.monotonic()

        # Start camera+ML on daemon thread
        self._detector_thread = threading.Thread(target=self._run_detector, daemon=True)
        self._detector_thread.start()

        self._refresh_monitoring_menu()

        self.title = " ✓"
        # Ensure we don't leave stale calibration text visible before first poll.
        self.status_item.title = "Monitoring…"

        _notify("Slouchy", "Monitoring started. Sit up straight!")

    def _stop_monitoring(self):
        """Stop detection and clean up."""
        if not self._monitoring:
            return

        self._monitoring = False
        self.detector.stop()

        # Log final streak
        if self._streak_start is not None:
            duration = time.monotonic() - self._streak_start
            if duration >= 60:
                self.tracker.log_good_streak(time.time(), duration)
            self._streak_start = None

        SlouchyApp._end_absence(self)
        SlouchyApp._end_tracking_session(self)

        self.title = " ⏸ Paused"
        self.status_item.title = "Paused"
        self._refresh_monitoring_menu()

    def _run_detector(self):
        """Detector thread entry point."""
        try:
            self.detector.run()
        except Exception as e:
            print(f"Camera thread crashed: {e}", file=sys.stderr)
            self._monitoring = False
            self.status_item.title = "Monitoring failed"
            self.title = " ❌"
            self._refresh_monitoring_menu()

    def _start_tracking_session(self) -> None:
        """Start a tracker session only when monitoring is actively running."""
        self._session_id = self.tracker.start_session()
        self._session_start = time.monotonic()

    def _end_tracking_session(self) -> None:
        """Close the active tracker session, if any."""
        if self._session_id is not None:
            self.tracker.end_session(self._session_id)
            self._session_id = None
        self._session_start = None

    def _end_absence(self) -> None:
        """Close the active absence period, if any."""
        if self._absence_id is not None:
            self.tracker.end_absence(self._absence_id, time.time())
            self._absence_id = None

    # ══════════════════════════════════════════════════════════════════
    # Poll callback (runs on rumps timer)
    # ══════════════════════════════════════════════════════════════════

    def _poll_callback(self, _timer):
        """Check posture state and update UI."""
        if not self._monitoring:
            return

        now = time.monotonic()
        if self._pause_until is not None:
            if now >= self._pause_until:
                # Timer expired!
                self._pause_until = None
                if self._session_id is None:
                    SlouchyApp._start_tracking_session(self)
                self._refresh_monitoring_menu()
                _notify("Slouchy", "Pause ended. Resuming posture monitoring!")
            else:
                # Still paused
                SlouchyApp._end_absence(self)
                SlouchyApp._end_tracking_session(self)
                remaining = self._pause_until - now
                self.title = f" ⏸ Paused ({_format_minutes(remaining)})"
                return

        with self.shared_state.lock:
            is_slouching = self.shared_state.is_slouching
            slouch_start = self.shared_state.slouch_start_time
            camera_ok = self.shared_state.camera_available
            confidence = self.shared_state.landmark_confidence
            in_motion = self.shared_state.in_motion

        if in_motion:
            self._end_absence()
            self.title = " ~"
            self.status_item.title = "Paused (in motion)"
            return

        if not camera_ok:
            self._end_absence()
            self.title = " ❌ Camera Error"
            self.status_item.title = "Camera unavailable"
            return

        if confidence < LANDMARK_CONFIDENCE_MIN:
            if self._absence_id is None:
                self._absence_id = self.tracker.start_absence(time.time())
            self.title = " ❓"
            self.status_item.title = "Tracking lost (stay in frame)"
            return
        else:
            self._end_absence()

        now = time.monotonic()
        tier = self.engine.update(is_slouching, slouch_start, now)

        # ── Handle audio trigger ──────────────────────────────────
        if tier is not None:
            self.audio.play_tier(tier)

            tier_names = {1: "gentle", 2: "firm", 3: "nuclear"}
            _notify("Slouchy", f"[{tier_names[tier]}] Mom is scolding you!")

            if slouch_start is not None:
                log_since = max(slouch_start, self._last_logged_time or slouch_start)
                duration = now - log_since
                self.tracker.log_slouch(time.time(), duration, tier)
                self._last_logged_time = now

        # ── Update streak tracking ────────────────────────────────
        if is_slouching:
            # Streak broken — log completed streak if substantial
            if self._streak_start is not None:
                streak_duration = now - self._streak_start
                if streak_duration >= 60:
                    self.tracker.log_good_streak(time.time(), streak_duration)
                self._streak_start = None
        else:
            if self._streak_start is None:
                self._streak_start = now

        # ── Update UI ─────────────────────────────────────────────
        self._update_menu_ui(is_slouching, slouch_start, now)

        # ── Session break reminder ────────────────────────────────
        if (
            self._session_start
            and (now - self._session_start) > SESSION_BREAK_HOURS * 3600
        ):
            _notify(
                "Slouchy",
                f"You've been working for {SESSION_BREAK_HOURS} hours. Take a break!",
            )
            self._session_start = now

    def _update_menu_ui(
        self, is_slouching: bool, slouch_start: float | None, now: float
    ):
        """Update the menubar title and status items."""
        if is_slouching:
            self.title = " ⚠"
            if slouch_start:
                elapsed = now - slouch_start
                self.status_item.title = f"Slouching ({_format_minutes(elapsed)})"
            else:
                self.status_item.title = "Slouching"

            self.streak_item.title = "Streak: 0s"
        else:
            self.title = " ✓"
            if self._streak_start:
                streak = now - self._streak_start
                self.streak_item.title = f"Streak: {_format_minutes(streak)}"
            else:
                self.streak_item.title = "Streak: 0s"

            self.status_item.title = "Good Posture"

    # ══════════════════════════════════════════════════════════════════
    # Stats refresh
    # ══════════════════════════════════════════════════════════════════

    def _refresh_stats(self, _timer=None):
        """Update the daily stats menu items."""
        summary = self.tracker.get_today_summary()
        slouch_min = summary["total_slouch_minutes"]
        monitor_min = summary["total_monitoring_minutes"]

        if monitor_min > 0:
            self.stats_item.title = (
                f"Today: {_format_minutes(slouch_min * 60)} slouch "
                f"/ {_format_minutes(monitor_min * 60)} total"
            )
        else:
            self.stats_item.title = "Today: No data yet"

    # ══════════════════════════════════════════════════════════════════
    # Menu callbacks
    # ══════════════════════════════════════════════════════════════════

    def _resume_monitoring(self, _sender):
        if self._monitoring and self._pause_until is None:
            self.status_item.title = "Already monitoring"
            self._refresh_monitoring_menu()
            return
        if not self.shared_state.is_calibrated:
            self._recalibrate(None)
        elif not self._monitoring:
            self._pause_until = None
            self._start_monitoring()
            self.status_item.title = "Monitoring resumed"
        else:
            self._pause_until = None
            if self._session_id is None:
                SlouchyApp._start_tracking_session(self)
            self.status_item.title = "Monitoring resumed"
        self._refresh_monitoring_menu()

    def _pause_monitoring(self, minutes_to_pause: int, _sender):
        """Pause monitoring for X minutes, or until 8 AM if -1."""
        if not self._monitoring and self._pause_until is None:
            # If not monitoring and no timer is set, we need to start monitoring
            # with the pause active.
            if not self.shared_state.is_calibrated:
                self._recalibrate(None)
            self._start_monitoring()

        now = time.monotonic()
        if minutes_to_pause == -1:
            # Until tomorrow 8:00 AM
            t = time.localtime()
            # Calculate seconds until 8:00 AM next day
            now_sec_today = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
            target_sec_today = 8 * 3600
            if now_sec_today < target_sec_today:
                seconds_until = target_sec_today - now_sec_today
            else:
                seconds_until = (24 * 3600 - now_sec_today) + target_sec_today
            self._pause_until = now + seconds_until
            self.status_item.title = "Paused until tomorrow"
        else:
            self._pause_until = now + (minutes_to_pause * 60)
            self.status_item.title = f"Paused for {minutes_to_pause}m"
        SlouchyApp._end_tracking_session(self)
        self._refresh_monitoring_menu()

    def _recalibrate(self, _sender):
        """Run calibration on a background thread."""
        was_monitoring = self._monitoring
        if was_monitoring:
            self._stop_monitoring()

        self.title = " Calibrating..."
        self.status_item.title = "Calibrating — sit up straight!"

        _notify("Slouchy", "Sit up straight for 5 seconds! Calibrating...")

        def _do_calibrate():
            time.sleep(2)  # give user time to sit up
            try:
                self.detector.calibrate()
                rumps.notification(
                    "Slouchy", "Calibration complete!", "Monitoring will start now."
                )
                self._start_monitoring()
            except Exception as e:
                rumps.notification("Slouchy", "Calibration failed", str(e))
                self.title = " Error"
                self.status_item.title = "Calibration failed"

        threading.Thread(target=_do_calibrate, daemon=True).start()

    def _set_camera(self, index: int, sender):
        """Switch OpenCV camera index."""
        self.detector.set_camera(index)

        # Update UI checks
        for i, item in self.camera_items.items():
            if i == index:
                item.title = f"● Camera {i}{' (Default)' if i == 0 else ''}"
            else:
                item.title = f"○ Camera {i}{' (Default)' if i == 0 else ''}"

    def _open_dashboard(self, _sender):
        """Render a local HTML dashboard and open it in the default browser."""
        today_summary = self.tracker.get_today_summary()
        recent_days = self.tracker.get_recent_days_summary(days=14)
        recent_events = self.tracker.get_all_events(limit=200)
        today_hourly = self.tracker.get_today_hourly_stats()
        html_doc = render_dashboard_html(today_summary, recent_days, recent_events, today_hourly)

        fd, path = tempfile.mkstemp(prefix="slouchy_dashboard_", suffix=".html")
        with os.fdopen(fd, "w") as f:
            f.write(html_doc)
        subprocess.run(["open", path], check=False)

    def _quit(self, _sender):
        """Clean shutdown."""
        self._stop_monitoring()

        # Print session summary to stdout (for debug)
        stats = self.tracker.get_daily_stats()
        total_min = stats["total_slouch_time"] / 60
        events = stats["total_events"]
        print(f"\nSession: {total_min:.1f} min slouch, {events} events")

        rumps.quit_application()


def main():
    os.makedirs(os.path.dirname(CALIBRATION_FILE), exist_ok=True)
    # Ensure auto-start is always enabled for users
    set_autostart(True)
    app = SlouchyApp()
    app.run()


if __name__ == "__main__":
    main()
