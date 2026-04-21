"""Posture detection and camera management.

Runs on a daemon background thread. Captures webcam frames, runs MediaPipe
pose detection, classifies posture, and updates a SharedState object.
"""

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions

from config import (
    CALIBRATION_DURATION_SECONDS,
    CALIBRATION_FILE,
    CHIN_DROP_THRESHOLD,
    EAR_TILT_THRESHOLD,
    EMA_ALPHA,
    EYE_TILT_THRESHOLD,
    HEAD_FORWARD_THRESHOLD,
    HEAD_TILT_THRESHOLD,
    HIP_CONFIDENCE_MIN,
    LANDMARK_CONFIDENCE_MIN,
    LOW_CONFIDENCE_FRAME_LIMIT,
    MODEL_PATH,
    MODEL_URL,
    POLL_INTERVAL_SECONDS,
    SLOUCH_ENTRY_STREAK,
    SLOUCH_EXIT_STREAK,
    SHOULDER_ROLL_THRESHOLD,
)
from motion import MotionDetector


def _ema(current: float, prev: float | None, alpha: float = EMA_ALPHA) -> float:
    """Exponential moving average. Returns `current` when `prev` is None (first call)."""
    if prev is None:
        return current
    return alpha * current + (1 - alpha) * prev

# MediaPipe landmark indices
NOSE = 0
LEFT_EYE = 2
RIGHT_EYE = 5
LEFT_EAR = 7
RIGHT_EAR = 8
LEFT_MOUTH = 9
RIGHT_MOUTH = 10
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_HIP = 23
RIGHT_HIP = 24

# Bump whenever metric definitions change so stale calibrations are rejected.
CALIBRATION_VERSION = 4


@dataclass
class SharedState:
    is_slouching: bool = False
    slouch_start_time: float | None = None
    good_posture_start_time: float | None = None
    landmark_confidence: float = 1.0
    camera_available: bool = True
    is_calibrated: bool = False
    in_motion: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class CalibrationError(Exception):
    """Raised when calibration fails (e.g. low landmark confidence)."""


class PostureDetector:
    """Captures frames, runs pose detection, classifies posture.

    Designed to run on a daemon thread via ``run()``.
    """

    def __init__(self, shared_state: SharedState, poll_interval: float = POLL_INTERVAL_SECONDS, camera_index: int = 0):
        self.state = shared_state
        self.poll_interval = poll_interval
        self._camera_index = camera_index

        # Calibration baselines
        self.baseline_nose_shoulder_ratio: float | None = None
        self.baseline_ear_shoulder_ratio: float | None = None
        self.baseline_shoulder_roll: float | None = None
        self.baseline_inter_shoulder_dist: float | None = None
        self.baseline_spine_angle: float | None = None
        self.baseline_head_forward: float | None = None
        self.baseline_eye_tilt: float | None = None
        self.baseline_chin_ratio: float | None = None

        # Low-confidence tracking
        self._low_confidence_count = 0

        # Hysteresis: consecutive good/bad frames needed to transition.
        # Entry and exit thresholds are configurable in config.py.
        self._good_streak = 0
        self._bad_streak = 0

        # EMA state for each metric (smoothed across poll cycles).
        self._ema_head_tilt: float | None = None
        self._ema_ear_tilt: float | None = None
        self._ema_head_forward: float | None = None
        self._ema_shoulder_roll: float | None = None
        self._ema_eye_tilt: float | None = None
        self._ema_chin_ratio: float | None = None

        # Motion detector (pauses posture checks while laptop is moving)
        self._motion_detector = MotionDetector()
        self._motion_detector.start()

        # Shutdown flag
        self._running = threading.Event()
        self._running.set()

        # Frame counter for VIDEO mode timestamp
        self._frame_ts_ms = 0

        # MediaPipe Pose Landmarker (Tasks API)
        self._landmarker = None  # created lazily or in run()/calibrate()

        # Camera
        self._cap: cv2.VideoCapture | None = None

        # Try to load existing calibration from disk
        self._load_calibration()

    def _ensure_model(self):
        """Download the pose model if it doesn't exist."""
        if os.path.exists(MODEL_PATH):
            return
        import urllib.request

        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        print("  → Downloading pose model (29 MB)... ", end="", flush=True)

        def _progress(block_num, block_size, total_size):
            if total_size > 0:
                pct = min(100, int(block_num * block_size * 100 / total_size))
                print(f"\r  → Downloading pose model (29 MB)... {pct}%", end="", flush=True)

        try:
            urllib.request.urlretrieve(MODEL_URL, MODEL_PATH, reporthook=_progress)
            print("\r  ✓ Pose model downloaded.                    ")
        except Exception as e:
            # Clean up partial download
            if os.path.exists(MODEL_PATH):
                os.remove(MODEL_PATH)
            raise RuntimeError(
                f"Failed to download pose model: {e}\n"
                f"Download it manually:\n"
                f"  curl -L -o {MODEL_PATH} {MODEL_URL}"
            ) from e

    def _create_landmarker(self):
        """Create a PoseLandmarker using the Tasks API."""
        self._ensure_model()
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            min_pose_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        return vision.PoseLandmarker.create_from_options(options)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_camera(self, index: int) -> None:
        """Switch to a new camera index."""
        self._camera_index = index
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @staticmethod
    def _configure_capture(cap: cv2.VideoCapture) -> None:
        """Best-effort low-latency camera configuration."""
        # Keep camera queues short to reduce visible end-to-end lag.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

    def _open_capture(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self._camera_index, cv2.CAP_AVFOUNDATION)
        self._configure_capture(cap)
        return cap

    @staticmethod
    def _read_latest_frame(cap: cv2.VideoCapture):
        """Read the newest available frame and drop a few stale buffered frames."""
        ret, frame = cap.read()
        if not ret or frame is None:
            return ret, frame

        # Drain a couple of queued frames when available to reduce camera lag.
        for _ in range(2):
            grabbed = cap.grab()
            if not grabbed:
                break
            retrieved = cap.retrieve()
            if not isinstance(retrieved, tuple) or len(retrieved) != 2:
                break
            ok, newest = retrieved
            if not ok or newest is None:
                break
            frame = newest

        return True, frame

    def run(self) -> None:
        """Main loop -- meant to run on a daemon thread."""
        self._running.set()
        self._landmarker = self._create_landmarker()
        self._frame_ts_ms = 0
        self._cap = self._open_capture()
        try:
            while self._running.is_set():
                loop_start = time.monotonic()
                if self._cap is None or not self._cap.isOpened():
                    self._cap = self._open_capture()

                ret, frame = self._read_latest_frame(self._cap)
                if not ret or frame is None:
                    with self.state.lock:
                        self.state.camera_available = False
                    time.sleep(self.poll_interval)
                    continue

                with self.state.lock:
                    self.state.camera_available = True

                self._motion_detector.feed_frame(frame)
                in_motion = self._motion_detector.is_in_motion()
                with self.state.lock:
                    self.state.in_motion = in_motion

                if not in_motion:
                    self._process_frame(frame)
                else:
                    # Reset bad-frame streak so the hysteresis gate doesn't fire
                    # the moment motion stops on the next good frame.
                    self._bad_streak = 0
                elapsed = time.monotonic() - loop_start
                remaining = self.poll_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    def stop(self) -> None:
        """Signal the run loop to exit."""
        self._running.clear()
        self._motion_detector.stop()

    def calibrate(self) -> None:
        """Capture frames for CALIBRATION_DURATION_SECONDS and compute baselines."""
        if self._cap is None or not self._cap.isOpened():
            self._cap = self._open_capture()

        cap = self._cap
        owns_cap = self._cap is None

        # Ensure model is available
        self._ensure_model()

        # Use IMAGE mode for calibration (independent frames, no timestamp tracking)
        cal_options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.IMAGE,
            min_pose_detection_confidence=0.5,
        )
        cal_landmarker = vision.PoseLandmarker.create_from_options(cal_options)

        nose_ratios: list[float] = []
        ear_ratios: list[float] = []
        shoulder_rolls: list[float] = []
        inter_shoulder_dists: list[float] = []
        spine_angles: list[float] = []
        head_forwards: list[float] = []
        eye_tilts: list[float] = []
        chin_ratios: list[float] = []

        start = time.monotonic()
        try:
            while time.monotonic() - start < CALIBRATION_DURATION_SECONDS:
                ret, frame = self._read_latest_frame(cap)
                if not ret or frame is None:
                    time.sleep(0.05)
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                results = cal_landmarker.detect(mp_image)
                if not results.pose_landmarks:
                    time.sleep(0.05)
                    continue

                landmarks = results.pose_landmarks[0]
                avg_confidence = self._avg_visibility(landmarks)
                if avg_confidence < LANDMARK_CONFIDENCE_MIN:
                    raise CalibrationError(
                        f"Landmark confidence too low during calibration: {avg_confidence:.2f}"
                    )

                nose_ratios.append(self._compute_head_tilt(landmarks))
                ear_ratios.append(self._compute_ear_shoulder_ratio(landmarks))
                shoulder_rolls.append(self._compute_shoulder_roll(landmarks))
                inter_shoulder_dists.append(self._inter_shoulder_distance(landmarks))
                head_forwards.append(self._compute_head_forward(landmarks))
                eye_tilts.append(self._compute_eye_tilt(landmarks))
                chin_ratios.append(self._compute_chin_ratio(landmarks))

                hip_conf = self._hip_confidence(landmarks)
                if hip_conf >= HIP_CONFIDENCE_MIN:
                    spine_angles.append(self._compute_spine_angle(landmarks))

                time.sleep(0.05)
        finally:
            if owns_cap:
                cap.release()

        if not nose_ratios:
            raise CalibrationError("No usable frames captured during calibration")

        self.baseline_nose_shoulder_ratio = sum(nose_ratios) / len(nose_ratios)
        self.baseline_ear_shoulder_ratio = sum(ear_ratios) / len(ear_ratios)
        self.baseline_shoulder_roll = sum(shoulder_rolls) / len(shoulder_rolls)
        self.baseline_inter_shoulder_dist = sum(inter_shoulder_dists) / len(inter_shoulder_dists)
        self.baseline_head_forward = sum(head_forwards) / len(head_forwards)
        self.baseline_eye_tilt = sum(eye_tilts) / len(eye_tilts)
        self.baseline_chin_ratio = sum(chin_ratios) / len(chin_ratios)
        if spine_angles:
            self.baseline_spine_angle = sum(spine_angles) / len(spine_angles)

        with self.state.lock:
            self.state.is_calibrated = True

        self._save_calibration()

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify(self, landmarks) -> bool:
        """Return True if slouching, False if good posture."""
        if self.baseline_nose_shoulder_ratio is None:
            return False  # not calibrated yet

        # Smooth raw metrics to reduce frame-to-frame noise near thresholds.
        self._ema_head_tilt = _ema(self._compute_head_tilt(landmarks), self._ema_head_tilt)
        self._ema_ear_tilt = _ema(self._compute_ear_shoulder_ratio(landmarks), self._ema_ear_tilt)
        self._ema_shoulder_roll = _ema(self._compute_shoulder_roll(landmarks), self._ema_shoulder_roll)
        head_tilt_ratio = self._ema_head_tilt
        ear_tilt_ratio = self._ema_ear_tilt
        shoulder_roll = self._ema_shoulder_roll

        # -- Head drop (scale-invariant) --
        # head_tilt is nose-to-shoulder y-distance / shoulder width,
        # so moving closer to the camera doesn't change the ratio.
        # When slouching, head drops → ratio DECREASES.
        head_deviation = (self.baseline_nose_shoulder_ratio - head_tilt_ratio) / self.baseline_nose_shoulder_ratio
        head_bad = head_deviation > HEAD_TILT_THRESHOLD

        # -- Ear drop (redundant signal, survives when nose is occluded) --
        ear_bad = False
        if self.baseline_ear_shoulder_ratio is not None:
            ear_deviation = (self.baseline_ear_shoulder_ratio - ear_tilt_ratio) / self.baseline_ear_shoulder_ratio
            ear_bad = ear_deviation > EAR_TILT_THRESHOLD

        # -- Head forward (relative z-depth) --
        # Relative z between nose and shoulders is stable because noise
        # cancels between nearby landmarks. Detects head jutting forward.
        head_forward_bad = False
        if self.baseline_head_forward is not None:
            self._ema_head_forward = _ema(self._compute_head_forward(landmarks), self._ema_head_forward)
            head_forward = self._ema_head_forward
            # When head juts forward, nose.z decreases relative to shoulders,
            # so (baseline - current) is positive.
            forward_deviation = self.baseline_head_forward - head_forward
            head_forward_bad = forward_deviation > HEAD_FORWARD_THRESHOLD

        # -- Shoulder forward roll --
        # When slouching, shoulders roll forward = angle INCREASES.
        shoulder_deviation = shoulder_roll - self.baseline_shoulder_roll
        shoulder_bad = shoulder_deviation > SHOULDER_ROLL_THRESHOLD

        # -- Secondary: spine angle (hip visibility) --
        spine_bad = False
        hip_conf = self._hip_confidence(landmarks)
        if hip_conf >= HIP_CONFIDENCE_MIN and self.baseline_spine_angle is not None:
            spine_angle = self._compute_spine_angle(landmarks)
            spine_deviation = abs(spine_angle - self.baseline_spine_angle)
            spine_bad = spine_deviation > SHOULDER_ROLL_THRESHOLD

        # -- Eye-line tilt (lateral head tilt) --
        eye_tilt_bad = False
        if self.baseline_eye_tilt is not None:
            self._ema_eye_tilt = _ema(self._compute_eye_tilt(landmarks), self._ema_eye_tilt)
            eye_tilt_deviation = abs(self._ema_eye_tilt - self.baseline_eye_tilt)
            eye_tilt_bad = eye_tilt_deviation > EYE_TILT_THRESHOLD

        # -- Chin drop (phone posture) --
        chin_bad = False
        if self.baseline_chin_ratio is not None and self.baseline_chin_ratio > 0:
            self._ema_chin_ratio = _ema(self._compute_chin_ratio(landmarks), self._ema_chin_ratio)
            chin_deviation = (self.baseline_chin_ratio - self._ema_chin_ratio) / self.baseline_chin_ratio
            chin_bad = chin_deviation > CHIN_DROP_THRESHOLD

        # Primary signals are generally stable and tied to true slouch posture.
        # Secondary signals are noisier and should corroborate each other.
        primary_bad_count = sum((head_bad, ear_bad, head_forward_bad, shoulder_bad))
        secondary_bad_count = sum((spine_bad, eye_tilt_bad, chin_bad))

        if primary_bad_count >= 1:
            return True
        return secondary_bad_count >= 2

    # ------------------------------------------------------------------
    # Measurement helpers
    # ------------------------------------------------------------------

    def _compute_head_tilt(self, landmarks) -> float:
        """Scale-invariant head tilt: nose-to-shoulder y-distance / shoulder width.

        Dividing by shoulder width makes this immune to perspective scaling
        (moving closer/further from camera). When slouching, the head drops
        so the y-distance shrinks relative to shoulder width → ratio decreases.
        """
        nose = landmarks[NOSE]
        l_sh = landmarks[LEFT_SHOULDER]
        r_sh = landmarks[RIGHT_SHOULDER]
        mid_y = (l_sh.y + r_sh.y) / 2
        y_dist = abs(nose.y - mid_y)
        shoulder_width = self._inter_shoulder_distance(landmarks)
        if shoulder_width == 0:
            return 0.0
        return y_dist / shoulder_width

    def _compute_ear_shoulder_ratio(self, landmarks) -> float:
        """Scale-invariant ear tilt: ear-midpoint-to-shoulder y-distance / shoulder width.

        Parallel signal to head tilt but using ears instead of nose. Ears
        stay detectable when the user looks down at a screen (nose can be
        partially occluded by the chin). Drops when the head tilts forward.
        """
        ear_y = (landmarks[LEFT_EAR].y + landmarks[RIGHT_EAR].y) / 2
        l_sh = landmarks[LEFT_SHOULDER]
        r_sh = landmarks[RIGHT_SHOULDER]
        mid_y = (l_sh.y + r_sh.y) / 2
        y_dist = abs(ear_y - mid_y)
        shoulder_width = self._inter_shoulder_distance(landmarks)
        if shoulder_width == 0:
            return 0.0
        return y_dist / shoulder_width

    def _compute_shoulder_roll(self, landmarks) -> float:
        """Angle (degrees) between ear-to-shoulder vector and vertical (2D only).

        Dropping z prevents noisy depth estimates from inflating the baseline
        angle (was ~50 deg with z; should be ~5-15 deg in 2D when upright).
        """
        ear_x = (landmarks[LEFT_EAR].x + landmarks[RIGHT_EAR].x) / 2
        ear_y = (landmarks[LEFT_EAR].y + landmarks[RIGHT_EAR].y) / 2

        sh_x = (landmarks[LEFT_SHOULDER].x + landmarks[RIGHT_SHOULDER].x) / 2
        sh_y = (landmarks[LEFT_SHOULDER].y + landmarks[RIGHT_SHOULDER].y) / 2

        dx = ear_x - sh_x
        dy = ear_y - sh_y

        # Angle from vertical (y axis points down in image coords)
        vec_len = math.sqrt(dx ** 2 + dy ** 2)
        if vec_len == 0:
            return 0.0
        cos_angle = abs(dy) / vec_len
        cos_angle = min(cos_angle, 1.0)
        return math.degrees(math.acos(cos_angle))

    def _compute_head_forward(self, landmarks) -> float:
        """Relative z-depth of nose vs shoulder midpoint.

        Absolute z from monocular estimation is noisy, but the *difference*
        between nearby landmarks is more stable (noise cancels).  When the
        head juts forward the nose z decreases relative to shoulders.
        """
        nose_z = landmarks[NOSE].z
        sh_z = (landmarks[LEFT_SHOULDER].z + landmarks[RIGHT_SHOULDER].z) / 2
        return nose_z - sh_z

    def _compute_spine_angle(self, landmarks) -> float:
        """Angle at the shoulder in the ear-shoulder-hip triangle (degrees)."""
        ear_x = (landmarks[LEFT_EAR].x + landmarks[RIGHT_EAR].x) / 2
        ear_y = (landmarks[LEFT_EAR].y + landmarks[RIGHT_EAR].y) / 2

        sh_x = (landmarks[LEFT_SHOULDER].x + landmarks[RIGHT_SHOULDER].x) / 2
        sh_y = (landmarks[LEFT_SHOULDER].y + landmarks[RIGHT_SHOULDER].y) / 2

        hip_x = (landmarks[LEFT_HIP].x + landmarks[RIGHT_HIP].x) / 2
        hip_y = (landmarks[LEFT_HIP].y + landmarks[RIGHT_HIP].y) / 2

        # Vectors from shoulder to ear and shoulder to hip
        v1 = (ear_x - sh_x, ear_y - sh_y)
        v2 = (hip_x - sh_x, hip_y - sh_y)

        dot = v1[0] * v2[0] + v1[1] * v2[1]
        mag1 = math.sqrt(v1[0] ** 2 + v1[1] ** 2)
        mag2 = math.sqrt(v2[0] ** 2 + v2[1] ** 2)
        if mag1 == 0 or mag2 == 0:
            return 0.0
        cos_a = max(-1.0, min(1.0, dot / (mag1 * mag2)))
        return math.degrees(math.acos(cos_a))

    def _compute_eye_tilt(self, landmarks) -> float:
        """Angle of the eye-line relative to horizontal (degrees).

        When the user tilts their head sideways (e.g. resting on hand),
        the eye-line rotates.  A perfectly level head ≈ 0 degrees.
        """
        l_eye = landmarks[LEFT_EYE]
        r_eye = landmarks[RIGHT_EYE]
        dx = r_eye.x - l_eye.x
        dy = r_eye.y - l_eye.y
        if abs(dx) < 1e-8:
            return 90.0
        return math.degrees(math.atan2(abs(dy), abs(dx)))

    def _compute_chin_ratio(self, landmarks) -> float:
        """Scale-invariant chin height: mouth-midpoint-to-shoulder y-distance / shoulder width.

        Similar to head tilt but using mouth landmarks.  Catches 'chin-to-chest'
        posture (looking down at phone) where the nose might not drop
        significantly but the mouth/chin clearly dips.
        """
        mouth_y = (landmarks[LEFT_MOUTH].y + landmarks[RIGHT_MOUTH].y) / 2
        l_sh = landmarks[LEFT_SHOULDER]
        r_sh = landmarks[RIGHT_SHOULDER]
        mid_y = (l_sh.y + r_sh.y) / 2
        y_dist = abs(mouth_y - mid_y)
        shoulder_width = self._inter_shoulder_distance(landmarks)
        if shoulder_width == 0:
            return 0.0
        return y_dist / shoulder_width

    def _inter_shoulder_distance(self, landmarks) -> float:
        l_sh = landmarks[LEFT_SHOULDER]
        r_sh = landmarks[RIGHT_SHOULDER]
        return math.sqrt(
            (l_sh.x - r_sh.x) ** 2 + (l_sh.y - r_sh.y) ** 2
        )

    def _hip_confidence(self, landmarks) -> float:
        return min(landmarks[LEFT_HIP].visibility, landmarks[RIGHT_HIP].visibility)

    def _avg_confidence(self, landmarks) -> float:
        vis = [lm.visibility for lm in landmarks]
        return sum(vis) / len(vis) if vis else 0.0

    def _avg_visibility(self, landmarks) -> float:
        """Average visibility for Tasks API NormalizedLandmark list."""
        vis = [lm.visibility for lm in landmarks]
        return sum(vis) / len(vis) if vis else 0.0

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    def _process_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        self._frame_ts_ms += int(self.poll_interval * 1000)
        results = self._landmarker.detect_for_video(mp_image, self._frame_ts_ms)

        if not results.pose_landmarks:
            self._handle_low_confidence(0.0)
            return

        landmarks = results.pose_landmarks[0]  # first person
        avg_confidence = self._avg_visibility(landmarks)

        with self.state.lock:
            self.state.landmark_confidence = avg_confidence

        if avg_confidence < LANDMARK_CONFIDENCE_MIN:
            self._handle_low_confidence(avg_confidence)
            return

        # Reset low-confidence counter on good frame
        self._low_confidence_count = 0

        if not self.state.is_calibrated:
            return

        slouching = self._classify(landmarks)

        # Two-sided hysteresis: configurable bad/good streaks to control
        # responsiveness vs stability.
        if slouching:
            self._bad_streak += 1
            self._good_streak = 0
        else:
            self._bad_streak = 0
            self._good_streak += 1

        with self.state.lock:
            now = time.monotonic()
            if slouching and not self.state.is_slouching and self._bad_streak >= SLOUCH_ENTRY_STREAK:
                # Transition: good -> slouch (after configured bad-frame streak)
                self.state.is_slouching = True
                self.state.slouch_start_time = now
                self.state.good_posture_start_time = None
            elif not slouching and self.state.is_slouching and self._good_streak >= SLOUCH_EXIT_STREAK:
                # Transition: slouch -> good (after configured good-frame streak)
                self.state.is_slouching = False
                self.state.slouch_start_time = None
                self.state.good_posture_start_time = now
            elif not slouching and not self.state.is_slouching and self.state.good_posture_start_time is None:
                self.state.good_posture_start_time = now

    def _handle_low_confidence(self, confidence: float) -> None:
        self._low_confidence_count += 1
        if self._low_confidence_count >= LOW_CONFIDENCE_FRAME_LIMIT:
            with self.state.lock:
                self.state.landmark_confidence = confidence

    # ------------------------------------------------------------------
    # Calibration persistence
    # ------------------------------------------------------------------

    def _save_calibration(self) -> None:
        data = {
            "version": CALIBRATION_VERSION,
            "baseline_nose_shoulder_ratio": self.baseline_nose_shoulder_ratio,
            "baseline_ear_shoulder_ratio": self.baseline_ear_shoulder_ratio,
            "baseline_shoulder_roll": self.baseline_shoulder_roll,
            "baseline_inter_shoulder_dist": self.baseline_inter_shoulder_dist,
            "baseline_spine_angle": self.baseline_spine_angle,
            "baseline_head_forward": self.baseline_head_forward,
            "baseline_eye_tilt": self.baseline_eye_tilt,
            "baseline_chin_ratio": self.baseline_chin_ratio,
        }
        os.makedirs(os.path.dirname(CALIBRATION_FILE), exist_ok=True)
        with open(CALIBRATION_FILE, "w") as f:
            json.dump(data, f)

    def _load_calibration(self) -> None:
        if not os.path.exists(CALIBRATION_FILE):
            return
        try:
            with open(CALIBRATION_FILE) as f:
                data = json.load(f)
            # Reject stale calibrations from older metric definitions.
            if data.get("version") != CALIBRATION_VERSION:
                return
            self.baseline_nose_shoulder_ratio = data.get("baseline_nose_shoulder_ratio")
            self.baseline_ear_shoulder_ratio = data.get("baseline_ear_shoulder_ratio")
            self.baseline_shoulder_roll = data.get("baseline_shoulder_roll")
            self.baseline_inter_shoulder_dist = data.get("baseline_inter_shoulder_dist")
            self.baseline_spine_angle = data.get("baseline_spine_angle")
            self.baseline_head_forward = data.get("baseline_head_forward")
            self.baseline_eye_tilt = data.get("baseline_eye_tilt")
            self.baseline_chin_ratio = data.get("baseline_chin_ratio")
            if self.baseline_nose_shoulder_ratio is not None:
                with self.state.lock:
                    self.state.is_calibrated = True
        except (json.JSONDecodeError, OSError):
            pass
