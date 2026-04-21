"""Tests for the camera-based MotionDetector in motion.py."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motion import MotionDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_detector(diff_threshold=20.0, window_size=10, min_samples=3):
    return MotionDetector(
        diff_threshold=diff_threshold,
        window_size=window_size,
        min_samples=min_samples,
    )


def _solid_bgr(height: int, width: int, b: int, g: int, r: int):
    """Return a solid-colour BGR frame as a uint8 numpy array."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (b, g, r)
    return frame


def _feed_n_identical(det, frame, n: int):
    """Feed the same frame n times (zero diff after the first pair)."""
    for _ in range(n):
        det.feed_frame(frame)


def _feed_alternating(det, frame_a, frame_b, count: int):
    """Alternate between two frames to simulate large per-pair diff."""
    for i in range(count):
        det.feed_frame(frame_a if i % 2 == 0 else frame_b)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_always_available(self):
        det = _make_detector()
        assert det.available is True

    def test_start_stop_noop(self):
        det = _make_detector()
        det.start()
        det.stop()  # no error


# ---------------------------------------------------------------------------
# No verdict before min_samples
# ---------------------------------------------------------------------------

class TestMinSamples:
    def test_no_verdict_before_min_samples(self):
        det = _make_detector(min_samples=3)
        frame = _solid_bgr(480, 640, 50, 50, 50)
        det.feed_frame(frame)  # establishes baseline, no diff yet
        det.feed_frame(frame)  # 1 diff recorded
        # 1 diff < min_samples=3 → no verdict
        assert det.is_in_motion() is False

    def test_verdict_at_exactly_min_samples(self):
        det = _make_detector(diff_threshold=20.0, window_size=10, min_samples=3)
        still = _solid_bgr(480, 640, 100, 100, 100)
        _feed_n_identical(det, still, 4)  # 3 diffs recorded (all 0)
        assert det.is_in_motion() is False  # zero diff, not in motion

    def test_no_motion_with_zero_samples(self):
        det = _make_detector()
        assert det.is_in_motion() is False


# ---------------------------------------------------------------------------
# Stationary scenes
# ---------------------------------------------------------------------------

class TestStationary:
    def test_identical_frames_not_in_motion(self):
        """Constant scene → zero diff → never in motion."""
        det = _make_detector(diff_threshold=20.0, window_size=10, min_samples=3)
        frame = _solid_bgr(480, 640, 80, 120, 60)
        _feed_n_identical(det, frame, 20)
        assert det.is_in_motion() is False

    def test_tiny_noise_not_in_motion(self):
        """Minor random noise in a static scene stays below threshold."""
        rng = np.random.default_rng(42)
        det = _make_detector(diff_threshold=20.0, window_size=10, min_samples=3)
        base = np.full((480, 640, 3), 128, dtype=np.uint8)
        for _ in range(20):
            noise = rng.integers(-2, 3, size=base.shape, dtype=np.int16)
            frame = np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)
            det.feed_frame(frame)
        assert det.is_in_motion() is False


# ---------------------------------------------------------------------------
# Motion scenes
# ---------------------------------------------------------------------------

class TestInMotion:
    def test_large_scene_change_detected(self):
        """Alternating bright/dark frames simulate whole-scene shift."""
        det = _make_detector(diff_threshold=20.0, window_size=10, min_samples=3)
        bright = _solid_bgr(480, 640, 200, 200, 200)
        dark = _solid_bgr(480, 640, 20, 20, 20)
        _feed_alternating(det, bright, dark, 15)
        assert det.is_in_motion() is True

    def test_walking_level_diff_detected(self):
        """Mean diff ~60 pixels (typical walking) is well above threshold."""
        det = _make_detector(diff_threshold=20.0, window_size=10, min_samples=3)
        frame_a = _solid_bgr(480, 640, 100, 100, 100)
        frame_b = _solid_bgr(480, 640, 160, 160, 160)  # diff = 60
        _feed_alternating(det, frame_a, frame_b, 15)
        assert det.is_in_motion() is True

    def test_lap_level_diff_detected(self):
        """Mean diff ~25 pixels (gentle lap rocking) exceeds the threshold."""
        det = _make_detector(diff_threshold=20.0, window_size=10, min_samples=3)
        frame_a = _solid_bgr(480, 640, 100, 100, 100)
        frame_b = _solid_bgr(480, 640, 125, 125, 125)  # diff = 25
        _feed_alternating(det, frame_a, frame_b, 15)
        assert det.is_in_motion() is True


# ---------------------------------------------------------------------------
# Threshold boundary
# ---------------------------------------------------------------------------

class TestThreshold:
    def test_exactly_at_threshold_is_not_motion(self):
        """avg == threshold is NOT in motion (strict >)."""
        det = _make_detector(diff_threshold=25.0, window_size=2, min_samples=2)
        # Each pair produces diff = 25 → avg = 25 → not > 25
        frame_a = _solid_bgr(1, 1, 0, 0, 0)
        frame_b = _solid_bgr(1, 1, 25, 25, 25)
        _feed_alternating(det, frame_a, frame_b, 4)
        assert det.is_in_motion() is False

    def test_just_above_threshold_is_motion(self):
        """avg = threshold + ε is in motion."""
        det = _make_detector(diff_threshold=25.0, window_size=2, min_samples=2)
        frame_a = _solid_bgr(1, 1, 0, 0, 0)
        frame_b = _solid_bgr(1, 1, 26, 26, 26)   # diff = 26 > 25
        _feed_alternating(det, frame_a, frame_b, 4)
        assert det.is_in_motion() is True


# ---------------------------------------------------------------------------
# Rolling window
# ---------------------------------------------------------------------------

class TestRollingWindow:
    def test_motion_clears_after_stillness(self):
        """After motion stops, window refills with small diffs → not in motion."""
        det = _make_detector(diff_threshold=20.0, window_size=5, min_samples=3)
        bright = _solid_bgr(480, 640, 200, 200, 200)
        dark = _solid_bgr(480, 640, 20, 20, 20)

        # Trigger motion
        _feed_alternating(det, bright, dark, 10)
        assert det.is_in_motion() is True

        # Refill window with identical frames
        still = _solid_bgr(480, 640, 100, 100, 100)
        _feed_n_identical(det, still, 10)
        assert det.is_in_motion() is False

    def test_window_size_limits_memory(self):
        """Only the most recent window_size diffs are kept."""
        det = _make_detector(diff_threshold=20.0, window_size=4, min_samples=1)
        bright = _solid_bgr(1, 1, 200, 200, 200)
        dark = _solid_bgr(1, 1, 20, 20, 20)

        # Fill with motion
        _feed_alternating(det, bright, dark, 10)
        assert len(det._diffs) <= 4

    def test_resolution_change_resets_baseline(self):
        """If frame dimensions change, previous gray is discarded gracefully."""
        det = _make_detector(min_samples=2)
        small = _solid_bgr(240, 320, 100, 100, 100)
        large = _solid_bgr(480, 640, 200, 200, 200)

        det.feed_frame(small)
        # Resolution change — previous frame discarded, diff not recorded
        det.feed_frame(large)
        assert len(det._diffs) == 0
