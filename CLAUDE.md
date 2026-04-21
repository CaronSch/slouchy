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

**`preferences.py`** — `Preferences` dataclass persisted to `~/.slouchy/preferences.json`. Covers sound alerts, desktop notifications, and active-hours scheduling. `load_preferences()` / `save_preferences()` are the only entry points; never write the JSON directly.

**`server.py`** — Minimal stdlib HTTP server on `localhost:47832`. Serves the dashboard on `GET /`; exposes `GET /api/preferences` and `POST /api/preferences` so the browser-based settings panel can read and write preferences. Runs on a daemon thread started at app init. Bound to `127.0.0.1` only.

**`dashboard.py`** — Generates a self-contained HTML page (no Flask, no templates). Called by the server on every `GET /` so stats are always fresh. Includes an interactive Settings section whose JS talks to `/api/preferences`.

**`config.py`** — Single source of truth for all thresholds, timings, paths, and the MediaPipe model URL. Check here before hardcoding any values.

### Threading Model

Three threads run concurrently:
- **Main thread** — rumps event loop + 100ms poll timer. Owns all UI mutations.
- **Detector thread** — MediaPipe inference + OpenCV capture. Writes to `SharedState` under `threading.Lock`. Started/stopped via `_acquire_camera()` / `_release_camera()`; camera is released when monitoring is paused or outside active hours.
- **HTTP server thread** — Serves dashboard HTML and handles preference API requests. Reads tracker (SQLite `check_same_thread=False`) and mutates `Preferences` under `_prefs_lock`.

### Calibration

First run auto-calibrates after 3s, capturing 5s of frames to compute baseline metrics saved to `~/.slouchy/calibration.json` (version-tagged). Stale calibrations (version mismatch) are rejected and recalibrated. Calibration normalizes per-user anatomy.

### Audio Customization

Users can drop `.wav` files into `phrases/tier1_gentle/`, `phrases/tier2_firm/`, `phrases/tier3_nuclear/` for custom voice phrases. The `generate_voice.py` (ElevenLabs API) and `generate_voice_f5.py` (local F5-TTS) scripts generate new phrase files.

### Release Build

`setup.py` configures py2app. The release bundles the MediaPipe model and phrase audio files into the `.app`. Resource paths are resolved via `RESOURCEPATH` env var (set by py2app) with fallback to source directory — `config.py` handles this transparently.
