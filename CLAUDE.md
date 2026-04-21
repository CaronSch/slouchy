# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install      # Create venv, install deps, pre-download MediaPipe model
make run          # Launch macOS menubar app (menubar.py)
make terminal     # Text-based terminal mode (main.py)
make debug        # Visual debug overlay with live pose landmarks (visualize.py)
make test         # Run pytest: python -m pytest tests/ -v
make release      # Build standalone .app + .dmg installer via py2app
make clean        # Clean build artifacts and pycache
```

Run a single test file: `python -m pytest tests/test_posture.py -v`

## Architecture

### Core Pipeline
```
Webcam → MediaPipe Pose Detection → 7-Signal Classification →
Hysteresis (EMA smoothing) → EscalationEngine state machine → AudioPlayer
```

### Key Components

**`posture.py`** — `PostureDetector` runs on a daemon background thread. Captures frames via OpenCV, feeds them to MediaPipe PoseLandmarker (heavy model), and computes 7 normalized posture signals. Updates `SharedState.is_slouching` under a threading lock. All signals are normalized by shoulder width for scale invariance across camera distances.

**7 posture signals:** head tilt, ear tilt (fallback when nose occluded), head forward (z-depth), shoulder roll (2D angle), spine angle (requires hip visibility ≥0.7), eye tilt (lateral tilt), chin drop (phone posture).

**`escalation.py`** — `EscalationEngine` state machine: `GOOD → SLOUCHING → TIER1 → TIER2 → TIER3`. Duration-based escalation (20s → 2min → 5min) and frequency-based escalation (3 gentle in 30min → tier 2). Hard reset: 10 continuous seconds of good posture returns to GOOD.

**`audio.py`** — `AudioPlayer` picks a random `.wav` from `phrases/tier{1,2,3}_*/` with a 3-level fallback chain ending at macOS system beep (`NSBeep()`).

**`tracker.py`** — SQLite at `~/.slouchy/posture.db` tracking slouch events and sessions. Computes daily/weekly stats, posture scores, streaks.

**`config.py`** — Single source of truth for all thresholds, timings, paths, and the MediaPipe model URL. Check here before hardcoding any values.

### Threading Model

The UI poll loop (100ms) runs on the main thread; MediaPipe inference runs on a daemon thread. All cross-thread state passes through `SharedState` guarded by `threading.Lock`.

### Calibration

First run auto-calibrates after 3s, capturing 5s of frames to compute baseline metrics saved to `~/.slouchy/calibration.json` (version-tagged). Stale calibrations (version mismatch) are rejected and recalibrated. Calibration normalizes per-user anatomy.

### Audio Customization

Users can drop `.wav` files into `phrases/tier1_gentle/`, `phrases/tier2_firm/`, `phrases/tier3_nuclear/` for custom voice phrases. The `generate_voice.py` (ElevenLabs API) and `generate_voice_f5.py` (local F5-TTS) scripts generate new phrase files.

### Release Build

`setup.py` configures py2app. The release bundles the MediaPipe model and phrase audio files into the `.app`. Resource paths are resolved via `RESOURCEPATH` env var (set by py2app) with fallback to source directory — `config.py` handles this transparently.
