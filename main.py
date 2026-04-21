"""Slouchy — Sit tall. Slouchy is watching.

Terminal-based app that uses webcam + MediaPipe to detect slouching,
then scolds you with your mother's voice in escalating tiers.
"""

import os
import subprocess
import signal
import threading
import time

from audio import AudioPlayer
from config import (
    CALIBRATION_FILE,
    POLL_INTERVAL_SECONDS,
    SESSION_BREAK_HOURS,
)
from escalation import EscalationEngine, State
from posture import PostureDetector, SharedState
from tracker import PostureTracker


def _notify(title, message):
    """Send a macOS notification."""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)


class Slouchy:
    def __init__(self):
        self.shared_state = SharedState()
        self.detector = PostureDetector(self.shared_state, poll_interval=POLL_INTERVAL_SECONDS)
        self.engine = EscalationEngine()
        self.audio = AudioPlayer()
        self.tracker = PostureTracker()

        self._session_id = None
        self._session_start = None
        self._monitoring = False
        self._last_logged_time = None

    def run(self):
        os.makedirs(os.path.dirname(CALIBRATION_FILE), exist_ok=True)

        print()
        print("  ╔══════════════════════════════════════╗")
        print("  ║ Sit tall. Slouchy is watching.     ║")
        print("  ╚══════════════════════════════════════╝")
        print()

        # Handle Ctrl+C gracefully
        signal.signal(signal.SIGINT, self._handle_quit)

        # Calibrate
        if not self.shared_state.is_calibrated:
            self._run_calibration()
            if not self.shared_state.is_calibrated:
                print("Cannot start without calibration. Exiting.")
                return

        # Start monitoring
        self._monitoring = True
        self._session_id = self.tracker.start_session()
        self._session_start = time.monotonic()

        # Start camera+ML on daemon thread
        def _run_detector():
            try:
                self.detector.run()
            except Exception as e:
                print(f"\n  ✗ Camera thread crashed: {e}")
                self._monitoring = False

        detector_thread = threading.Thread(target=_run_detector, daemon=True)
        detector_thread.start()

        _notify("Slouchy", "Monitoring started. Sit up straight!")
        print("  ✓ Monitoring started. Sit up straight!")
        print("  Press Ctrl+C to stop.")
        print()

        # Poll loop on main thread
        try:
            while self._monitoring:
                self._check_posture()
                time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop()

    def _run_calibration(self):
        print("  → Calibrating: sit up straight and look at your screen...")
        _notify("Slouchy", "Sit up straight for 5 seconds! Calibrating...")
        time.sleep(2)

        try:
            self.detector.calibrate()
            print("  ✓ Calibration complete!")
            _notify("Slouchy", "Calibration complete!")
        except Exception as e:
            print(f"  ✗ Calibration failed: {e}")
            print("  Make sure your camera can see your face and shoulders.")
            _notify("Slouchy", f"Calibration failed: {e}")

    def _stop(self):
        self._monitoring = False
        self.detector.stop()
        if self._session_id is not None:
            self.tracker.end_session(self._session_id)

        # Print daily stats
        stats = self.tracker.get_daily_stats()
        total_min = stats["total_slouch_time"] / 60
        events = stats["total_events"]
        print()
        print("  ╔══════════════════════════════════════╗")
        print("  ║          Session Summary             ║")
        print("  ╠══════════════════════════════════════╣")
        print(f"  ║  Total slouch time: {total_min:>5.1f} min        ║")
        print(f"  ║  Slouch events:     {events:>5}            ║")
        print(f"  ║  Tier 1 (gentle):   {stats['tier_counts'][1]:>5}            ║")
        print(f"  ║  Tier 2 (firm):     {stats['tier_counts'][2]:>5}            ║")
        print(f"  ║  Tier 3 (nuclear):  {stats['tier_counts'][3]:>5}            ║")
        print("  ╚══════════════════════════════════════╝")
        print()

    def _handle_quit(self, signum, frame):
        self._monitoring = False

    def _check_posture(self):
        with self.shared_state.lock:
            is_slouching = self.shared_state.is_slouching
            slouch_start = self.shared_state.slouch_start_time
            camera_ok = self.shared_state.camera_available
            confidence = self.shared_state.landmark_confidence
            in_motion = self.shared_state.in_motion

        if not camera_ok:
            return
        if confidence < 0.5:
            return
        if in_motion:
            print("  ~ Moving...")
            return

        now = time.monotonic()
        tier = self.engine.update(is_slouching, slouch_start, now)

        if tier is not None:
            self.audio.play_tier(tier)
            tier_names = {1: "gentle", 2: "FIRM", 3: "NUCLEAR"}
            print(f"  🔊 [{tier_names[tier]}] Mom is scolding you!")

            if slouch_start is not None:
                log_since = max(slouch_start, self._last_logged_time or slouch_start)
                duration = now - log_since
                self.tracker.log_slouch(time.time(), duration, tier)
                self._last_logged_time = now
        else:
            if self.engine.state == State.GOOD:
                if is_slouching is False:
                    print(f"  ✓ Good posture")
            elif self.engine.state == State.SLOUCHING:
                if slouch_start:
                    elapsed = now - slouch_start
                    print(f"  ⚠ Slouching... ({elapsed:.0f}s)")

        # Session break reminder
        if self._session_start and (now - self._session_start) > SESSION_BREAK_HOURS * 3600:
            _notify("Slouchy", f"You've been working for {SESSION_BREAK_HOURS} hours. Take a break!")
            print(f"\n  ⏰ Take a break! {SESSION_BREAK_HOURS} hours of work.")
            self._session_start = now


def main():
    app = Slouchy()
    app.run()


if __name__ == "__main__":
    main()
