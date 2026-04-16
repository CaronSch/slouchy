"""Slouchy configuration. Single source of truth for all settings."""

import os

# Polling
# Faster detection/UI refresh for more immediate feedback in menubar and terminal modes.
POLL_INTERVAL_SECONDS = 0.1

# Posture detection thresholds (relative to calibrated baseline).
# Loosened from the initial tuning: signals are OR'd together, so each needs
# enough slack for natural micro-movements without flagging a seated-straight user.
HEAD_TILT_THRESHOLD = 0.056  # 5.6% deviation in nose-to-shoulder ratio (scale-invariant)
EAR_TILT_THRESHOLD = 0.056  # 5.6% deviation in ear-to-shoulder ratio (scale-invariant)
HEAD_FORWARD_THRESHOLD = 0.028  # relative z-depth deviation (nose vs shoulders)
SHOULDER_ROLL_THRESHOLD = 4.2  # degrees from calibrated upright
EYE_TILT_THRESHOLD = 3.5  # degrees — lateral head tilt (eye-line off horizontal)
CHIN_DROP_THRESHOLD = 0.07  # 7% drop in mouth-to-shoulder ratio (phone posture)
HIP_CONFIDENCE_MIN = 0.7  # minimum confidence to use hip landmarks
LANDMARK_CONFIDENCE_MIN = 0.3  # below this = "can't see you"
LOW_CONFIDENCE_FRAME_LIMIT = 10  # consecutive low-confidence frames before warning
# Transition hysteresis in detector loop.
# At 0.1s polling this is ~0.3s to enter slouch and ~0.8s to recover.
# This keeps the UI responsive while preventing rapid flip-flops on noisy frames.
SLOUCH_ENTRY_STREAK = 3
SLOUCH_EXIT_STREAK = 8

# Detection smoothing (exponential moving average on raw metrics)
# Applied per poll cycle; higher alpha = less smoothing, faster response.
EMA_ALPHA = 0.5

# Calibration
CALIBRATION_DURATION_SECONDS = 5
CALIBRATION_FILE = os.path.expanduser("~/.slouchy/calibration.json")

# Escalation thresholds
SLOUCH_TIER1_SECONDS = 20
SLOUCH_TIER2_SECONDS = 120  # 2 minutes
SLOUCH_TIER3_SECONDS = 300  # 5 minutes
TIER1_FREQUENCY_LIMIT = 3  # 3rd gentle in 30 min triggers tier 2
TIER1_FREQUENCY_WINDOW = 1800  # 30 minutes
TIER2_FREQUENCY_LIMIT = 2  # 2nd firm in 1 hour triggers tier 3
TIER2_FREQUENCY_WINDOW = 3600  # 1 hour

# Cooldowns
TIER1_COOLDOWN_SECONDS = 300  # 5 minutes
TIER2_COOLDOWN_SECONDS = 600  # 10 minutes
TIER3_COOLDOWN_SECONDS = 1200  # 20 minutes

# State reset
GOOD_POSTURE_RESET_SECONDS = 10  # continuous good posture to reset to GOOD

# Phrase directories
if os.environ.get("RESOURCEPATH"):
    # Running inside a Py2app bundle
    PHRASES_DIR = os.path.join(os.environ.get("RESOURCEPATH"), "phrases")
    BASE_MODEL_DIR = os.path.join(os.environ.get("RESOURCEPATH"), "models")
else:
    # Running from source (terminal / script form)
    PHRASES_DIR = os.path.join(os.path.dirname(__file__), "phrases")
    BASE_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")

TIER_DIRS = {
    1: os.path.join(PHRASES_DIR, "tier1_gentle"),
    2: os.path.join(PHRASES_DIR, "tier2_firm"),
    3: os.path.join(PHRASES_DIR, "tier3_nuclear"),
}

# MediaPipe model (heavy = ~3-5x more accurate landmarks than lite; ~30ms extra per frame)
MODEL_PATH = os.path.join(BASE_MODEL_DIR, "pose_landmarker_heavy.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"
)

# Database (must be outside the read-only .app bundle)
DB_PATH = os.path.expanduser("~/.slouchy/posture.db")

# Session duration warning
SESSION_BREAK_HOURS = 3
