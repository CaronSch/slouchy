"""Motion detection via camera frame-difference analysis.

CoreMotion is unavailable on native macOS apps (Apple-confirmed limitation).
Instead, laptop motion is detected by measuring frame-to-frame pixel changes
in the webcam feed — since the camera is physically attached to the lid, any
laptop movement produces large scene shifts that stand out from ordinary
subject movement.

Call ``feed_frame()`` with each BGR frame, then query ``is_in_motion()``.
No background thread is needed; the caller drives the updates.
"""

import logging

import cv2

from config import (
    MOTION_FRAME_DIFF_THRESHOLD,
    MOTION_FRAME_DIFF_WINDOW,
    MOTION_MIN_FRAME_DIFF_SAMPLES,
)

logger = logging.getLogger(__name__)


class MotionDetector:
    """Detects laptop motion using consecutive camera frame differences.

    A stationary laptop on a desk produces very small frame-to-frame pixel
    changes (noise only). Laptop movement — walking, resting on an unstable
    lap — shifts the entire scene and produces large mean absolute
    differences across all pixels.

    The rolling window of recent differences smooths over brief spikes and
    micro-movements so that minor user gestures at a desk do not trigger a
    false positive.
    """

    def __init__(
        self,
        diff_threshold: float = MOTION_FRAME_DIFF_THRESHOLD,
        window_size: int = MOTION_FRAME_DIFF_WINDOW,
        min_samples: int = MOTION_MIN_FRAME_DIFF_SAMPLES,
    ):
        self._threshold = diff_threshold
        self._window_size = window_size
        self._min_samples = min_samples
        self._diffs: list[float] = []  # rolling window (capped at window_size)
        self._prev_gray = None
        self._last_motion_state = False

    @property
    def available(self) -> bool:
        """Always True — camera-based detection works on every Mac."""
        return True

    def start(self) -> None:
        """No-op: no background thread needed; caller drives via feed_frame()."""

    def stop(self) -> None:
        """No-op: nothing to tear down."""

    def feed_frame(self, frame) -> None:
        """Ingest a BGR camera frame and update the rolling diff window."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            return
        diff = cv2.absdiff(gray, self._prev_gray)
        self._prev_gray = gray
        mean_diff = cv2.mean(diff)[0]
        self._diffs.append(mean_diff)
        if len(self._diffs) > self._window_size:
            self._diffs.pop(0)

    def is_in_motion(self) -> bool:
        """Return True when sustained camera movement is detected.

        Returns False until at least ``min_samples`` frames have been fed,
        so the window has time to fill before issuing a verdict.
        """
        if len(self._diffs) < self._min_samples:
            return False
        avg = sum(self._diffs) / len(self._diffs)
        in_motion = avg > self._threshold
        if in_motion != self._last_motion_state:
            if in_motion:
                logger.info("Motion detected (avg_diff=%.1f, threshold=%.1f)", avg, self._threshold)
            else:
                logger.info("Motion settled (avg_diff=%.1f, threshold=%.1f)", avg, self._threshold)
            self._last_motion_state = in_motion
        return in_motion
