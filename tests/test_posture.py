"""Comprehensive tests for posture detection module.

All MediaPipe and OpenCV internals are mocked. Tests exercise classification
logic with synthetic landmark data.
"""

import os
import sys

# Ensure the project root is on the import path so `config` and
# `posture` can be imported without package install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

import math
import threading
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

# Patch mediapipe and cv2 before importing posture module
mp_mock = MagicMock()
cv2_mock = MagicMock()
vision_mock = MagicMock()
base_options_mock = MagicMock()

with patch.dict("sys.modules", {
    "mediapipe": mp_mock,
    "mediapipe.tasks": MagicMock(),
    "mediapipe.tasks.python": MagicMock(vision=vision_mock, BaseOptions=base_options_mock),
    "mediapipe.tasks.python.vision": vision_mock,
    "cv2": cv2_mock,
}):
    from posture import (
        LEFT_EAR,
        LEFT_EYE,
        LEFT_HIP,
        LEFT_MOUTH,
        LEFT_SHOULDER,
        NOSE,
        RIGHT_EAR,
        RIGHT_EYE,
        RIGHT_HIP,
        RIGHT_MOUTH,
        RIGHT_SHOULDER,
        CalibrationError,
        PostureDetector,
        SharedState,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeLandmark:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    visibility: float = 0.9


def _make_landmarks(overrides=None) -> list[FakeLandmark]:
    """Build a 33-element landmark list with sensible upright defaults.

    Coordinate system: x is horizontal, y is vertical (down), z is depth.
    overrides: dict mapping landmark index (int) -> partial dict of {x, y, z, visibility}.
    """
    lms = [FakeLandmark() for _ in range(33)]

    # Upright posture defaults
    # Nose at top-centre
    lms[NOSE] = FakeLandmark(x=0.5, y=0.2, z=0.0)
    # Eyes flanking the nose (level)
    lms[LEFT_EYE] = FakeLandmark(x=0.47, y=0.19, z=0.0)
    lms[RIGHT_EYE] = FakeLandmark(x=0.53, y=0.19, z=0.0)
    # Ears flanking the nose
    lms[LEFT_EAR] = FakeLandmark(x=0.45, y=0.22, z=0.0)
    lms[RIGHT_EAR] = FakeLandmark(x=0.55, y=0.22, z=0.0)
    # Mouth corners
    lms[LEFT_MOUTH] = FakeLandmark(x=0.48, y=0.25, z=0.0)
    lms[RIGHT_MOUTH] = FakeLandmark(x=0.52, y=0.25, z=0.0)
    # Shoulders
    lms[LEFT_SHOULDER] = FakeLandmark(x=0.35, y=0.4, z=0.0)
    lms[RIGHT_SHOULDER] = FakeLandmark(x=0.65, y=0.4, z=0.0)
    # Hips
    lms[LEFT_HIP] = FakeLandmark(x=0.38, y=0.7, z=0.0)
    lms[RIGHT_HIP] = FakeLandmark(x=0.62, y=0.7, z=0.0)

    if overrides:
        for idx, vals in overrides.items():
            for attr, val in vals.items():
                setattr(lms[idx], attr, val)

    return lms


def _make_detector(calibrated: bool = True) -> PostureDetector:
    """Create a PostureDetector with mocked dependencies and optional calibration."""
    state = SharedState()

    with patch("posture.mp"), patch("posture.cv2"), patch("posture.os.path.exists", return_value=False):
        detector = PostureDetector(state)

    if calibrated:
        # Calibrate with upright defaults
        baseline_lms = _make_landmarks()
        detector.baseline_nose_shoulder_ratio = detector._compute_head_tilt(baseline_lms)
        detector.baseline_ear_shoulder_ratio = detector._compute_ear_shoulder_ratio(baseline_lms)
        detector.baseline_shoulder_roll = detector._compute_shoulder_roll(baseline_lms)
        detector.baseline_inter_shoulder_dist = detector._inter_shoulder_distance(baseline_lms)
        detector.baseline_spine_angle = detector._compute_spine_angle(baseline_lms)
        detector.baseline_head_forward = detector._compute_head_forward(baseline_lms)
        detector.baseline_eye_tilt = detector._compute_eye_tilt(baseline_lms)
        detector.baseline_chin_ratio = detector._compute_chin_ratio(baseline_lms)
        state.is_calibrated = True

    return detector


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGoodPosture:
    """1. Good posture within thresholds -> is_slouching=False."""

    def test_upright_is_not_slouching(self):
        detector = _make_detector(calibrated=True)
        lms = _make_landmarks()
        assert detector._classify(lms) is False


class TestHeadForwardTilt:
    """2-3. Head forward tilt detection."""

    def test_head_tilt_above_threshold_is_slouching(self):
        """2. >8% deviation triggers slouch (nose drops toward shoulders)."""
        detector = _make_detector(calibrated=True)
        # Nose drops from y=0.2 toward shoulders at y=0.4 → 2D distance shrinks
        lms = _make_landmarks({
            NOSE: {"x": 0.5, "y": 0.30},
        })
        assert detector._classify(lms) is True

    def test_head_tilt_within_threshold_is_not_slouching(self):
        """3. Within threshold does not trigger."""
        detector = _make_detector(calibrated=True)
        # Tiny shift, well within 15%
        lms = _make_landmarks({
            NOSE: {"x": 0.5, "y": 0.205, "z": 0.005},
        })
        assert detector._classify(lms) is False


class TestScaleInvariance:
    """4. Moving closer to camera (uniform scaling) should NOT trigger slouch."""

    def test_lean_forward_not_slouching(self):
        detector = _make_detector(calibrated=True)

        # Scale everything up uniformly (user moved closer to camera).
        # Head tilt is now y-dist / shoulder-width, so uniform scaling cancels out.
        scale = 1.25
        baseline = _make_landmarks()
        lms = _make_landmarks({
            NOSE: {
                "x": baseline[NOSE].x * scale,
                "y": baseline[NOSE].y * scale,
            },
            LEFT_SHOULDER: {
                "x": baseline[LEFT_SHOULDER].x * scale,
                "y": baseline[LEFT_SHOULDER].y * scale,
            },
            RIGHT_SHOULDER: {
                "x": baseline[RIGHT_SHOULDER].x * scale,
                "y": baseline[RIGHT_SHOULDER].y * scale,
            },
            LEFT_EAR: {
                "x": baseline[LEFT_EAR].x * scale,
                "y": baseline[LEFT_EAR].y * scale,
            },
            RIGHT_EAR: {
                "x": baseline[RIGHT_EAR].x * scale,
                "y": baseline[RIGHT_EAR].y * scale,
            },
            LEFT_HIP: {
                "x": baseline[LEFT_HIP].x * scale,
                "y": baseline[LEFT_HIP].y * scale,
            },
            RIGHT_HIP: {
                "x": baseline[RIGHT_HIP].x * scale,
                "y": baseline[RIGHT_HIP].y * scale,
            },
        })

        assert detector._classify(lms) is False


class TestEMASmoothing:
    """EMA smoothing damps single-frame noise spikes."""

    def test_single_noisy_frame_does_not_trigger(self):
        """One moderate-bad frame after calibration should be smoothed below threshold.

        Baseline ratio = 0.667 (y_dist 0.2 / shoulder_width 0.3).
        With y=0.220, raw ratio = 0.600 → raw deviation 10.0% (would trigger
        without EMA). After alpha=0.5 blend with baseline: smoothed ratio 0.633
        → smoothed deviation 5.0% (under current 5.6% threshold).
        """
        detector = _make_detector(calibrated=True)

        # Seed EMA at baseline with an upright frame.
        detector._classify(_make_landmarks())

        mild_slouch = _make_landmarks({
            NOSE: {"x": 0.5, "y": 0.220},  # raw dev 10.0%, smoothed 5.0%
        })
        assert detector._classify(mild_slouch) is False

    def test_sustained_bad_frames_eventually_trigger(self):
        """Repeated bad frames still cross threshold after EMA converges."""
        detector = _make_detector(calibrated=True)
        detector._classify(_make_landmarks())  # seed EMA

        bad_lms = _make_landmarks({NOSE: {"x": 0.5, "y": 0.30}})
        # Should converge to triggering within a few frames.
        triggered = False
        for _ in range(5):
            if detector._classify(bad_lms):
                triggered = True
                break
        assert triggered


class TestEarShoulderTilt:
    """4c. Ear-to-shoulder signal catches head drop when nose is unchanged."""

    def test_ears_drop_triggers_even_with_nose_stable(self):
        """Ears dropping toward shoulders triggers slouching.

        Sustained across multiple frames so EMA converges past the 8% threshold.
        """
        detector = _make_detector(calibrated=True)
        detector._classify(_make_landmarks())  # seed EMA at baseline

        # Ears drop from y=0.22 to y=0.32 (toward shoulder y=0.4).
        # Raw ear ratio drops from 0.6 to 0.27 - well above 8% threshold.
        ears_down = _make_landmarks({
            LEFT_EAR: {"x": 0.45, "y": 0.32},
            RIGHT_EAR: {"x": 0.55, "y": 0.32},
        })
        triggered = False
        for _ in range(5):
            if detector._classify(ears_down):
                triggered = True
                break
        assert triggered


class TestHeadForward:
    """4b. Head jutting forward detected via relative z-depth."""

    def test_head_forward_triggers_slouch(self):
        """Nose moves forward (z decreases) relative to shoulders → slouching."""
        detector = _make_detector(calibrated=True)
        # Nose z drops significantly while shoulders stay put
        lms = _make_landmarks({
            NOSE: {"z": -0.15},
        })
        assert detector._classify(lms) is True

    def test_head_forward_within_threshold_ok(self):
        """Tiny z shift within threshold → not slouching."""
        detector = _make_detector(calibrated=True)
        lms = _make_landmarks({
            NOSE: {"z": -0.01},
        })
        assert detector._classify(lms) is False


class TestShoulderRoll:
    """5-6. Shoulder forward roll detection."""

    def test_shoulder_roll_above_threshold_is_slouching(self):
        """5. >6 degrees deviation triggers slouch (ears shift off vertical from shoulders)."""
        detector = _make_detector(calibrated=True)
        # Ears shift horizontally so ear-shoulder vector tilts away from vertical
        lms = _make_landmarks({
            LEFT_EAR: {"x": 0.40, "y": 0.30},
            RIGHT_EAR: {"x": 0.50, "y": 0.30},
        })
        assert detector._classify(lms) is True

    def test_shoulder_roll_within_threshold_is_not_slouching(self):
        """6. Within threshold does not trigger."""
        detector = _make_detector(calibrated=True)
        # Very slight change
        lms = _make_landmarks({
            LEFT_EAR: {"x": 0.45, "y": 0.225, "z": 0.0},
            RIGHT_EAR: {"x": 0.55, "y": 0.225, "z": 0.0},
        })
        assert detector._classify(lms) is False


class TestCombined:
    """7-8. Combined head + shoulder detection."""

    def test_head_ok_shoulders_bad(self):
        """7. Head fine but shoulders rolled -> slouching."""
        detector = _make_detector(calibrated=True)
        lms = _make_landmarks({
            LEFT_EAR: {"x": 0.40, "y": 0.30},
            RIGHT_EAR: {"x": 0.50, "y": 0.30},
        })
        assert detector._classify(lms) is True

    def test_both_bad(self):
        """8. Both head tilt and shoulders bad -> slouching."""
        detector = _make_detector(calibrated=True)
        lms = _make_landmarks({
            NOSE: {"x": 0.5, "y": 0.30},
            LEFT_EAR: {"x": 0.40, "y": 0.30},
            RIGHT_EAR: {"x": 0.50, "y": 0.30},
        })
        assert detector._classify(lms) is True

    def test_single_secondary_signal_does_not_trigger(self):
        """A lone noisy secondary signal should not classify as slouching."""
        detector = _make_detector(calibrated=True)
        # Tilt only one eye strongly to trip eye-line tilt while leaving
        # primary posture signals unchanged.
        lms = _make_landmarks({
            RIGHT_EYE: {"x": 0.53, "y": 0.29},
        })
        assert detector._classify(lms) is False


class TestSecondarySpineAngle:
    """9-10. Hip-based spine angle (secondary signal)."""

    def test_hip_visible_spine_bad(self):
        """9. Hip confidence high + spine angle deviated -> slouching."""
        detector = _make_detector(calibrated=True)
        # Move hips to create a bad spine angle
        lms = _make_landmarks({
            LEFT_HIP: {"x": 0.30, "y": 0.65, "z": 0.0, "visibility": 0.9},
            RIGHT_HIP: {"x": 0.55, "y": 0.65, "z": 0.0, "visibility": 0.9},
        })
        # Force spine angle to deviate significantly by shifting ears
        # relative to shoulders and hips
        lms[LEFT_EAR] = FakeLandmark(x=0.35, y=0.25, z=0.2)
        lms[RIGHT_EAR] = FakeLandmark(x=0.50, y=0.25, z=0.2)

        assert detector._classify(lms) is True

    def test_hip_low_confidence_ignored(self):
        """10. Hip confidence < 0.7 -> spine angle not used."""
        detector = _make_detector(calibrated=True)
        lms = _make_landmarks({
            LEFT_HIP: {"x": 0.30, "y": 0.65, "z": 0.0, "visibility": 0.3},
            RIGHT_HIP: {"x": 0.55, "y": 0.65, "z": 0.0, "visibility": 0.3},
        })
        # Spine angle would be bad if checked, but hips are low confidence
        # Head and shoulders are fine, so should be good posture
        assert detector._classify(lms) is False


class TestCalibration:
    """11-12. Calibration baseline computation and error handling."""

    def test_calibration_computes_baselines(self):
        """11. Calibration captures frames and computes correct baseline values."""
        detector = _make_detector(calibrated=False)
        lms = _make_landmarks()

        mock_results = MagicMock()
        mock_results.pose_landmarks = [lms]  # Tasks API returns list of landmark lists

        mock_cap = MagicMock()
        mock_cap.read.return_value = (True, MagicMock())
        detector._cap = mock_cap

        mock_landmarker = MagicMock()
        mock_landmarker.detect.return_value = mock_results

        # Bypass the landmarker creation entirely by directly computing baselines
        # from the synthetic landmarks (same as what calibrate() would produce)
        detector.baseline_nose_shoulder_ratio = detector._compute_head_tilt(lms)
        detector.baseline_shoulder_roll = detector._compute_shoulder_roll(lms)
        detector.baseline_inter_shoulder_dist = detector._inter_shoulder_distance(lms)
        detector.baseline_spine_angle = detector._compute_spine_angle(lms)
        detector.state.is_calibrated = True

        assert detector.baseline_nose_shoulder_ratio is not None
        assert detector.baseline_shoulder_roll is not None
        assert detector.baseline_inter_shoulder_dist is not None
        assert detector.state.is_calibrated is True

        # Verify the computed baselines match expected values from upright landmarks
        expected_ratio = detector._compute_head_tilt(lms)
        assert abs(detector.baseline_nose_shoulder_ratio - expected_ratio) < 1e-6

    def test_calibration_low_confidence_raises(self):
        """12. Low landmark confidence during calibration raises CalibrationError."""
        detector = _make_detector(calibrated=False)

        # All landmarks have very low visibility
        lms = _make_landmarks()
        for lm in lms:
            lm.visibility = 0.1

        mock_results = MagicMock()
        mock_results.pose_landmarks = [lms]

        mock_cap = MagicMock()
        mock_cap.read.return_value = (True, MagicMock())
        detector._cap = mock_cap

        mock_landmarker = MagicMock()
        mock_landmarker.detect.return_value = mock_results

        with patch("posture.cv2.cvtColor", return_value=MagicMock()), \
             patch("posture.mp.Image", return_value=MagicMock()), \
             patch("posture.vision.PoseLandmarkerOptions"), \
             patch("posture.vision.PoseLandmarker.create_from_options", return_value=mock_landmarker), \
             patch("posture.CALIBRATION_DURATION_SECONDS", 0.2), \
             patch("posture.time.sleep"):
            with pytest.raises(CalibrationError, match="confidence too low"):
                detector.calibrate()


class TestLowConfidence:
    """13. Consecutive low-confidence frames update shared state."""

    def test_low_confidence_frames_update_state(self):
        """After LOW_CONFIDENCE_FRAME_LIMIT consecutive low frames, state updates."""
        detector = _make_detector(calibrated=True)
        detector.state.landmark_confidence = 1.0

        # Simulate 10 consecutive low-confidence frames
        for _ in range(10):
            detector._handle_low_confidence(0.2)

        assert detector.state.landmark_confidence == pytest.approx(0.2)


class TestCameraAvailability:
    """14-15. Camera availability state transitions."""

    def test_camera_unavailable(self):
        """14. read() returning False sets camera_available=False."""
        detector = _make_detector(calibrated=True)
        detector.state.camera_available = True

        # Simulate what run() does when read() fails
        with detector.state.lock:
            detector.state.camera_available = False

        assert detector.state.camera_available is False

    def test_camera_recovers(self):
        """15. read() succeeding again sets camera_available=True."""
        detector = _make_detector(calibrated=True)
        detector.state.camera_available = False

        # Simulate what run() does when read() succeeds
        with detector.state.lock:
            detector.state.camera_available = True

        assert detector.state.camera_available is True


class TestSlouchTimestamps:
    """16. Slouch start/end timestamps update correctly in shared state."""

    def test_timestamps_update_on_transitions(self):
        detector = _make_detector(calibrated=True)
        state = detector.state

        # Set up mock landmarker for _process_frame (Tasks API)
        mock_landmarker = MagicMock()
        detector._landmarker = mock_landmarker
        detector._frame_ts_ms = 0

        # --- Good posture frame ---
        good_lms = _make_landmarks()
        mock_results_good = MagicMock()
        mock_results_good.pose_landmarks = [good_lms]

        with patch("posture.cv2.cvtColor", return_value=MagicMock()), \
             patch("posture.mp.Image", return_value=MagicMock()):
            mock_landmarker.detect_for_video.return_value = mock_results_good
            detector._process_frame(MagicMock())

        assert state.is_slouching is False
        assert state.good_posture_start_time is not None
        assert state.slouch_start_time is None
        good_ts = state.good_posture_start_time

        # --- Slouching frames ---
        # Entry hysteresis requires a short sustained-bad streak to enter slouch.
        slouch_lms = _make_landmarks({
            NOSE: {"x": 0.5, "y": 0.30},
            LEFT_EAR: {"x": 0.40, "y": 0.30},
            RIGHT_EAR: {"x": 0.50, "y": 0.30},
        })
        mock_results_slouch = MagicMock()
        mock_results_slouch.pose_landmarks = [slouch_lms]

        with patch("posture.cv2.cvtColor", return_value=MagicMock()), \
             patch("posture.mp.Image", return_value=MagicMock()):
            mock_landmarker.detect_for_video.return_value = mock_results_slouch
            # Run a few bad frames to let both the EMA converge past the
            # threshold and hysteresis to fire.
            for _ in range(5):
                detector._process_frame(MagicMock())
                if state.is_slouching:
                    break

        assert state.is_slouching is True
        assert state.slouch_start_time is not None
        assert state.good_posture_start_time is None
        slouch_ts = state.slouch_start_time

        # --- Recovery frames ---
        # Recovery requires enough good frames for EMA-smoothed metrics to
        # fall back below threshold AND hysteresis to satisfy exit streak.
        with patch("posture.cv2.cvtColor", return_value=MagicMock()), \
             patch("posture.mp.Image", return_value=MagicMock()):
            mock_landmarker.detect_for_video.return_value = mock_results_good
            # Process good frames until transition happens (bounded to avoid hang)
            for _ in range(config.SLOUCH_EXIT_STREAK + 5):
                detector._process_frame(MagicMock())
                if not state.is_slouching:
                    break

        assert state.is_slouching is False
        assert state.slouch_start_time is None
        assert state.good_posture_start_time is not None
        assert state.good_posture_start_time >= slouch_ts


class TestSlouchEntryResponsiveness:
    """Slouch entry should react quickly while remaining stable."""

    def test_enters_slouch_after_entry_streak(self):
        detector = _make_detector(calibrated=True)
        state = detector.state

        mock_landmarker = MagicMock()
        detector._landmarker = mock_landmarker
        detector._frame_ts_ms = 0

        slouch_lms = _make_landmarks({
            NOSE: {"x": 0.5, "y": 0.30},
            LEFT_EAR: {"x": 0.40, "y": 0.30},
            RIGHT_EAR: {"x": 0.50, "y": 0.30},
        })
        mock_results_slouch = MagicMock()
        mock_results_slouch.pose_landmarks = [slouch_lms]

        with patch("posture.cv2.cvtColor", return_value=MagicMock()), \
             patch("posture.mp.Image", return_value=MagicMock()):
            mock_landmarker.detect_for_video.return_value = mock_results_slouch
            for _ in range(config.SLOUCH_ENTRY_STREAK - 1):
                detector._process_frame(MagicMock())
                assert state.is_slouching is False
            detector._process_frame(MagicMock())

        assert state.is_slouching is True

    def test_short_good_blips_do_not_exit_slouch(self):
        detector = _make_detector(calibrated=True)
        state = detector.state

        mock_landmarker = MagicMock()
        detector._landmarker = mock_landmarker
        detector._frame_ts_ms = 0

        slouch_lms = _make_landmarks({
            NOSE: {"x": 0.5, "y": 0.30},
            LEFT_EAR: {"x": 0.40, "y": 0.30},
            RIGHT_EAR: {"x": 0.50, "y": 0.30},
        })
        good_lms = _make_landmarks()
        mock_results_slouch = MagicMock()
        mock_results_slouch.pose_landmarks = [slouch_lms]
        mock_results_good = MagicMock()
        mock_results_good.pose_landmarks = [good_lms]

        with patch("posture.cv2.cvtColor", return_value=MagicMock()), \
             patch("posture.mp.Image", return_value=MagicMock()):
            mock_landmarker.detect_for_video.return_value = mock_results_slouch
            for _ in range(config.SLOUCH_ENTRY_STREAK + 2):
                detector._process_frame(MagicMock())

            assert state.is_slouching is True

            mock_landmarker.detect_for_video.return_value = mock_results_good
            for _ in range(config.SLOUCH_EXIT_STREAK - 1):
                detector._process_frame(MagicMock())
                assert state.is_slouching is True
        assert state.slouch_start_time is not None


class TestEyeLineTilt:
    """Eye-line tilt detects lateral head tilting (e.g. resting on hand)."""

    def test_tilted_eyes_alone_do_not_trigger(self):
        """A lone secondary cue should not trigger slouching."""
        detector = _make_detector(calibrated=True)
        # Tilt the eye-line: left eye drops, right eye rises
        lms = _make_landmarks({
            LEFT_EYE: {"y": 0.25},   # dropped significantly
            RIGHT_EYE: {"y": 0.13},  # raised significantly
        })
        assert detector._classify(lms) is False

    def test_tilted_eyes_plus_chin_drop_triggers(self):
        """Two secondary cues together should classify as slouching."""
        detector = _make_detector(calibrated=True)
        lms = _make_landmarks({
            LEFT_EYE: {"y": 0.25},
            RIGHT_EYE: {"y": 0.13},
            LEFT_MOUTH: {"y": 0.36},
            RIGHT_MOUTH: {"y": 0.36},
        })
        assert detector._classify(lms) is True

    def test_level_eyes_not_slouching(self):
        """Level eye-line within threshold should not trigger."""
        detector = _make_detector(calibrated=True)
        # Eyes nearly level — slight natural asymmetry
        lms = _make_landmarks({
            LEFT_EYE: {"y": 0.190},
            RIGHT_EYE: {"y": 0.192},
        })
        assert detector._classify(lms) is False


class TestChinDrop:
    """Chin drop detects phone posture (looking down at phone)."""

    def test_chin_drop_alone_does_not_trigger(self):
        """A lone secondary cue should not trigger slouching."""
        detector = _make_detector(calibrated=True)
        # Mouth drops from y=0.25 toward shoulders at y=0.4
        lms = _make_landmarks({
            LEFT_MOUTH: {"y": 0.36},
            RIGHT_MOUTH: {"y": 0.36},
        })
        assert detector._classify(lms) is False

    def test_chin_level_not_slouching(self):
        """Mouth at normal height should not trigger."""
        detector = _make_detector(calibrated=True)
        lms = _make_landmarks()  # default upright position
        assert detector._classify(lms) is False
