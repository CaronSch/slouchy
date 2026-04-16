"""Visual posture debug tool with live audio scolding.

Shows the webcam with landmarks + measurements, tracks slouch duration,
and triggers tier-based audio scolding (same as main.py) when you slouch.
"""

import argparse
import cv2
import math
import time
import mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions

from audio import AudioPlayer
from escalation import EscalationEngine, State
from config import (
    CALIBRATION_DURATION_SECONDS,
    EAR_TILT_THRESHOLD,
    HEAD_FORWARD_THRESHOLD,
    HEAD_TILT_THRESHOLD,
    SHOULDER_ROLL_THRESHOLD,
    MODEL_PATH,
    LANDMARK_CONFIDENCE_MIN,
)

# Landmark indices
NOSE = 0
LEFT_EAR = 7
RIGHT_EAR = 8
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_HIP = 23
RIGHT_HIP = 24

CONNECTIONS = [
    (LEFT_EAR, LEFT_SHOULDER), (RIGHT_EAR, RIGHT_SHOULDER),
    (LEFT_SHOULDER, RIGHT_SHOULDER), (LEFT_SHOULDER, LEFT_HIP),
    (RIGHT_SHOULDER, RIGHT_HIP), (LEFT_HIP, RIGHT_HIP),
    (NOSE, LEFT_SHOULDER), (NOSE, RIGHT_SHOULDER),
]


def compute_head_tilt(lms):
    """Scale-invariant: nose-to-shoulder y-distance / shoulder width."""
    nose = lms[NOSE]
    l_sh, r_sh = lms[LEFT_SHOULDER], lms[RIGHT_SHOULDER]
    mid_y = (l_sh.y + r_sh.y) / 2
    y_dist = abs(nose.y - mid_y)
    sh_width = math.sqrt((l_sh.x - r_sh.x)**2 + (l_sh.y - r_sh.y)**2)
    if sh_width == 0:
        return 0.0
    return y_dist / sh_width


def compute_head_forward(lms):
    """Relative z-depth: nose vs shoulder midpoint."""
    nose_z = lms[NOSE].z
    sh_z = (lms[LEFT_SHOULDER].z + lms[RIGHT_SHOULDER].z) / 2
    return nose_z - sh_z


def compute_ear_shoulder_ratio(lms):
    """Scale-invariant: ear-to-shoulder y-distance / shoulder width."""
    ear_y = (lms[LEFT_EAR].y + lms[RIGHT_EAR].y) / 2
    l_sh, r_sh = lms[LEFT_SHOULDER], lms[RIGHT_SHOULDER]
    mid_y = (l_sh.y + r_sh.y) / 2
    y_dist = abs(ear_y - mid_y)
    sh_width = math.sqrt((l_sh.x - r_sh.x)**2 + (l_sh.y - r_sh.y)**2)
    if sh_width == 0:
        return 0.0
    return y_dist / sh_width


def compute_shoulder_roll(lms):
    ear_x = (lms[LEFT_EAR].x + lms[RIGHT_EAR].x) / 2
    ear_y = (lms[LEFT_EAR].y + lms[RIGHT_EAR].y) / 2
    sh_x = (lms[LEFT_SHOULDER].x + lms[RIGHT_SHOULDER].x) / 2
    sh_y = (lms[LEFT_SHOULDER].y + lms[RIGHT_SHOULDER].y) / 2
    dx, dy = ear_x - sh_x, ear_y - sh_y
    vec_len = math.sqrt(dx**2 + dy**2)
    if vec_len == 0:
        return 0.0
    cos_angle = min(abs(dy) / vec_len, 1.0)
    return math.degrees(math.acos(cos_angle))


def avg_visibility(lms):
    vis = [lm.visibility for lm in lms]
    return sum(vis) / len(vis) if vis else 0.0


def _configure_capture(cap: cv2.VideoCapture) -> None:
    """Best-effort low-latency camera settings."""
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass


def _read_latest_frame(cap: cv2.VideoCapture):
    """Read the newest available frame and drop stale queued frames."""
    ret, frame = cap.read()
    if not ret or frame is None:
        return ret, frame

    for _ in range(2):
        grabbed = cap.grab()
        if not grabbed:
            break
        ok, newest = cap.retrieve()
        if not ok or newest is None:
            break
        frame = newest

    return True, frame


def _parse_args():
    parser = argparse.ArgumentParser(description="Slouchy live visualizer")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--mute-audio", action="store_true")
    return parser.parse_args()


def main():
    args = _parse_args()

    cap = cv2.VideoCapture(args.camera_index, cv2.CAP_AVFOUNDATION)
    _configure_capture(cap)
    if not cap.isOpened():
        print("Cannot open camera")
        return

    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        min_pose_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = vision.PoseLandmarker.create_from_options(options)

    baseline_tilt = None
    baseline_ear = None
    baseline_roll = None
    baseline_forward = None
    frame_ts = 0
    calibrated = False
    calibrating = False
    calibration_start = 0.0
    calibration_samples = []

    # Audio + escalation
    audio = None if args.mute_audio else AudioPlayer()
    engine = EscalationEngine()
    slouch_start = None
    good_streak = 0

    # Per-frame EMA state (alpha tuned for ~30fps, ~2s effective window).
    ema_tilt = None
    ema_ear = None
    ema_fwd = None
    ema_roll = None
    frame_alpha = 0.1

    GREEN = (0, 200, 0)
    RED = (0, 0, 255)
    YELLOW = (0, 200, 255)
    WHITE = (255, 255, 255)
    CYAN = (255, 255, 0)

    print(f"Press 'c' to calibrate (hold still for {CALIBRATION_DURATION_SECONDS}s)")
    print("Press 'q' to quit")

    while True:
        ret, frame = _read_latest_frame(cap)
        if not ret:
            break

        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        now_ts = int(time.monotonic() * 1000)
        frame_ts = max(frame_ts + 1, now_ts)
        results = landmarker.detect_for_video(mp_image, frame_ts)

        if results.pose_landmarks:
            lms = results.pose_landmarks[0]
            confidence = avg_visibility(lms)
            tracking_ok = confidence >= LANDMARK_CONFIDENCE_MIN

            # Draw skeleton
            for i, j in CONNECTIONS:
                x1, y1 = int(lms[i].x * w), int(lms[i].y * h)
                x2, y2 = int(lms[j].x * w), int(lms[j].y * h)
                cv2.line(frame, (x1, y1), (x2, y2), CYAN, 2)

            # Draw key landmarks
            for idx in [NOSE, LEFT_EAR, RIGHT_EAR, LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP]:
                x, y = int(lms[idx].x * w), int(lms[idx].y * h)
                cv2.circle(frame, (x, y), 6, GREEN, -1)
                cv2.circle(frame, (x, y), 6, WHITE, 1)

            # Draw nose-to-shoulder-midpoint line (head tilt signal)
            nose_x, nose_y = int(lms[NOSE].x * w), int(lms[NOSE].y * h)
            mid_sh_x = int((lms[LEFT_SHOULDER].x + lms[RIGHT_SHOULDER].x) / 2 * w)
            mid_sh_y = int((lms[LEFT_SHOULDER].y + lms[RIGHT_SHOULDER].y) / 2 * h)
            cv2.line(frame, (nose_x, nose_y), (mid_sh_x, mid_sh_y), YELLOW, 3)

            # Compute raw measurements (shown on-screen for diagnostic value)
            tilt_raw = compute_head_tilt(lms)
            ear_raw = compute_ear_shoulder_ratio(lms)
            roll_raw = compute_shoulder_roll(lms)
            fwd_raw = compute_head_forward(lms)

            # Multi-frame calibration to avoid one-frame baseline noise.
            if calibrating and tracking_ok:
                calibration_samples.append((tilt_raw, ear_raw, roll_raw, fwd_raw))
            if calibrating and (time.monotonic() - calibration_start) >= CALIBRATION_DURATION_SECONDS:
                calibrating = False
                if len(calibration_samples) >= 10:
                    baseline_tilt = sum(s[0] for s in calibration_samples) / len(calibration_samples)
                    baseline_ear = sum(s[1] for s in calibration_samples) / len(calibration_samples)
                    baseline_roll = sum(s[2] for s in calibration_samples) / len(calibration_samples)
                    baseline_forward = sum(s[3] for s in calibration_samples) / len(calibration_samples)
                    calibrated = True
                    slouch_start = None
                    good_streak = 0
                    engine = EscalationEngine()
                    # Seed EMA with calibrated values so first-frame deltas are stable.
                    ema_tilt = baseline_tilt
                    ema_ear = baseline_ear
                    ema_roll = baseline_roll
                    ema_fwd = baseline_forward
                    print(
                        f"Calibrated ({len(calibration_samples)} frames)! "
                        f"Tilt: {baseline_tilt:.3f}, ear: {baseline_ear:.3f}, "
                        f"roll: {baseline_roll:.1f} deg, forward: {baseline_forward:.3f}"
                    )
                else:
                    print(
                        "Calibration failed: not enough confident frames. "
                        "Make sure your face and shoulders are visible, then press 'c' again."
                    )
                calibration_samples.clear()

            # Smooth with EMA; first frame initializes the state.
            ema_tilt = tilt_raw if ema_tilt is None else frame_alpha * tilt_raw + (1 - frame_alpha) * ema_tilt
            ema_ear = ear_raw if ema_ear is None else frame_alpha * ear_raw + (1 - frame_alpha) * ema_ear
            ema_fwd = fwd_raw if ema_fwd is None else frame_alpha * fwd_raw + (1 - frame_alpha) * ema_fwd
            ema_roll = roll_raw if ema_roll is None else frame_alpha * roll_raw + (1 - frame_alpha) * ema_roll

            # Classification uses smoothed values; overlay still shows raw too.
            tilt, ear, fwd, roll = ema_tilt, ema_ear, ema_fwd, ema_roll

            # Determine posture
            is_slouching = False
            head_bad = False
            ear_bad = False
            forward_bad = False
            shoulder_bad = False

            if calibrated and not calibrating and tracking_ok:
                head_dev = (baseline_tilt - tilt) / baseline_tilt
                head_bad = head_dev > HEAD_TILT_THRESHOLD
                ear_dev = (baseline_ear - ear) / baseline_ear
                ear_bad = ear_dev > EAR_TILT_THRESHOLD
                forward_dev = baseline_forward - fwd
                forward_bad = forward_dev > HEAD_FORWARD_THRESHOLD
                shoulder_dev_val = roll - baseline_roll
                shoulder_bad = shoulder_dev_val > SHOULDER_ROLL_THRESHOLD
                is_slouching = head_bad or ear_bad or forward_bad or shoulder_bad

                # --- Hysteresis + slouch timer ---
                now = time.monotonic()
                if is_slouching:
                    good_streak = 0
                    if slouch_start is None:
                        slouch_start = now
                elif good_streak >= 2:
                    slouch_start = None
                else:
                    good_streak += 1

                # --- Drive escalation engine, play audio on tier triggers ---
                tier = engine.update(is_slouching, slouch_start, now)
                if tier is not None:
                    if audio is not None:
                        audio.play_tier(tier)
                    tier_names = {1: "gentle", 2: "FIRM", 3: "NUCLEAR"}
                    print(f"  [{tier_names[tier]}] Mom is scolding you!")
            elif not tracking_ok:
                is_slouching = False

            # Status panel background
            cv2.rectangle(frame, (10, 10), (420, 280), (0, 0, 0), -1)
            cv2.rectangle(frame, (10, 10), (420, 280), WHITE, 1)

            # Status text
            if calibrating:
                remaining = max(0.0, CALIBRATION_DURATION_SECONDS - (time.monotonic() - calibration_start))
                status = f"CALIBRATING ({remaining:.1f}s)"
                status_color = YELLOW
            elif not tracking_ok:
                status = "TRACKING LOST"
                status_color = YELLOW
            elif calibrated and slouch_start is not None:
                elapsed = time.monotonic() - slouch_start
                status = f"SLOUCHING ({elapsed:.0f}s)"
                status_color = RED
            elif is_slouching:
                status = "SLOUCHING"
                status_color = RED
            else:
                status = "GOOD POSTURE"
                status_color = GREEN
            cv2.putText(frame, status, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, status_color, 2)

            if calibrated and tracking_ok:
                head_dev_pct = (baseline_tilt - tilt) / baseline_tilt * 100
                head_color = RED if head_bad else GREEN
                cv2.putText(frame, f"Head tilt: {head_dev_pct:.1f}% (limit: {HEAD_TILT_THRESHOLD*100:.0f}%)",
                            (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.55, head_color, 1)

                ear_dev_pct = (baseline_ear - ear) / baseline_ear * 100
                ear_color = RED if ear_bad else GREEN
                cv2.putText(frame, f"Ear tilt: {ear_dev_pct:.1f}% (limit: {EAR_TILT_THRESHOLD*100:.0f}%)",
                            (20, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.55, ear_color, 1)

                fwd_color = RED if forward_bad else GREEN
                cv2.putText(frame, f"Head forward: {forward_dev:.3f} (limit: {HEAD_FORWARD_THRESHOLD:.3f})",
                            (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.55, fwd_color, 1)

                roll_dev = roll - baseline_roll
                roll_color = RED if shoulder_bad else GREEN
                cv2.putText(frame, f"Shoulder roll: {roll_dev:.1f} deg (limit: {SHOULDER_ROLL_THRESHOLD:.0f} deg)",
                            (20, 155), cv2.FONT_HERSHEY_SIMPLEX, 0.55, roll_color, 1)

                cv2.putText(frame, f"Tilt: {baseline_tilt:.3f}->{tilt:.3f}  Ear: {baseline_ear:.3f}->{ear:.3f}",
                            (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.40, WHITE, 1)
                cv2.putText(frame, f"Fwd: {baseline_forward:.3f}->{fwd:.3f}  Roll: {baseline_roll:.1f}->{roll:.1f}",
                            (20, 205), cv2.FONT_HERSHEY_SIMPLEX, 0.40, WHITE, 1)

                # Engine state
                state_colors = {
                    State.GOOD: GREEN, State.SLOUCHING: YELLOW,
                    State.TIER1: (0, 165, 255), State.TIER2: (0, 100, 255), State.TIER3: RED,
                }
                state_color = state_colors.get(engine.state, WHITE)
                cv2.putText(frame, f"Engine: {engine.state.name}",
                            (20, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.55, state_color, 1)
            elif calibrated and not tracking_ok:
                cv2.putText(
                    frame,
                    f"Confidence: {confidence:.2f} (need >= {LANDMARK_CONFIDENCE_MIN:.2f})",
                    (20, 95),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    YELLOW,
                    1,
                )
                cv2.putText(
                    frame,
                    "Keep your face + shoulders fully in frame",
                    (20, 120),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    YELLOW,
                    1,
                )
            else:
                cv2.putText(frame, f"Press 'c' to calibrate ({CALIBRATION_DURATION_SECONDS}s)", (20, 80),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, YELLOW, 1)
                cv2.putText(frame, f"Tilt: {tilt:.3f}  Roll: {roll:.1f} deg  Fwd: {fwd:.3f}",
                            (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1)

            # Posture bar
            if calibrated and tracking_ok:
                bar_x = 10
                bar_y = 290
                bar_w = 410
                bar_h = 25
                cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)
                max_dev = max(head_dev_pct / (HEAD_TILT_THRESHOLD * 100),
                              ear_dev_pct / (EAR_TILT_THRESHOLD * 100),
                              forward_dev / HEAD_FORWARD_THRESHOLD,
                              roll_dev / SHOULDER_ROLL_THRESHOLD) if calibrated else 0
                fill = min(max_dev, 2.0) / 2.0
                fill_w = int(fill * bar_w)
                bar_color = GREEN if fill < 0.5 else YELLOW if fill < 1.0 else RED
                cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), bar_color, -1)
                cv2.putText(frame, "POSTURE", (bar_x + 5, bar_y + 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1)

        cv2.imshow("Slouchy - Posture Debug", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("c"):
            calibrating = True
            calibration_start = time.monotonic()
            calibration_samples.clear()
            print(
                f"Calibrating... hold still for {CALIBRATION_DURATION_SECONDS}s "
                "(face and shoulders in frame)"
            )

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
