# Slouchy

**Sit tall. Slouchy is watching.**

A macOS menubar app that watches you through your webcam and scolds you in an
escalating mother's-voice when you slouch. Runs entirely on your machine — no
frames, images, or data ever leave your computer.

```
Webcam → MediaPipe Pose → Classify → Escalation Engine → Audio Scolding
```

## Prerequisites

- **macOS 13+** (Ventura or later)
- **Python 3.10+** (tested on 3.11)
- A working **webcam** (built-in or external)

## Quick Start

```bash
git clone https://github.com/nidhi-singh02/slouchy.git Slouchy
cd Slouchy
./install.sh    # creates venv, installs deps, downloads model
make run        # launches the menubar app
```

That's it. macOS will prompt you to grant **Camera** access on the first run.



## How to Use

### Menubar App (recommended)

```bash
python menubar.py
```

A small icon appears in your macOS menubar. Its symbol changes by state
(`✓` good posture, `⚠` slouching, `❓` tracking lost, `❌` camera issue, `⏸` paused).

Click the icon to see:
- **Status** — current posture state
- **Streak** — how long you've maintained good posture
- **Today's stats** — slouch time and monitoring time
- **Monitoring submenu** — resume, pause 30m, pause 1h, pause until tomorrow
- **Recalibrate** — re-capture your "good posture" baseline
- **Open Dashboard** — view trends and recent events
- **Camera** — pick camera index (0-3)

### Terminal Mode

```bash
python main.py
```

Text-based interface that prints posture updates to stdout. Useful for
debugging or running headless.

### Visual Debug Overlay

```bash
python visualize.py
```

Opens an OpenCV window with live webcam, skeleton overlay, per-signal metrics,
a posture deviation bar, and the escalation engine state. Press `c` to
calibrate, `q` to quit.

### Download DMG directly
Direct download URLs:
- Portfolio (primary): `https://nidhisingh.pages.dev/slouchy/Slouchy.dmg`
- GitHub Releases (latest): `https://github.com/nidhi-singh02/slouchy/releases/latest/download/Slouchy.dmg`

## How It Works

### 1. Calibration (5 seconds)

On first launch (or when you click "Recalibrate"), sit up straight for
5 seconds. The app captures your baseline posture. Stored at
`~/.slouchy/calibration.json` with a version tag that auto-rejects stale
baselines when metric definitions change.

### 2. Detection — Seven Independent Signals

Any one signal exceeding its threshold triggers slouching:

| Signal | What it measures | What it catches |
|--------|-----------------|-----------------|
| Head tilt | nose→shoulder y-distance / shoulder width | Head dropping |
| Ear tilt | ear→shoulder y-distance / shoulder width | Head drop (nose occluded) |
| Head forward | nose z – shoulder z (relative depth) | Head jutting forward |
| Shoulder roll | 2D ear-shoulder angle from vertical | Rounded shoulders |
| Spine angle | ear→shoulder→hip angle (when hips visible) | Torso lean |
| Eye tilt | left-eye ↔ right-eye angle | Sideways head tilt |
| Chin drop | mouth→shoulder y-distance / shoulder width | Phone-neck posture |

All metrics are **scale-invariant** (dividing by shoulder width), so moving
closer/further from the camera doesn't trigger false positives.

Raw values are **EMA-smoothed** and require a **2-frame streak** to transition
(hysteresis), so single noisy frames don't flip the state.

### 3. Escalation (State Machine)

```
GOOD → SLOUCHING → TIER 1 (gentle) → TIER 2 (firm) → TIER 3 (nuclear)
          10s          2 min              5 min
```

**Frequency escalation:** 3 gentle warnings in 30 min → tier 2. 2 firm
warnings in 1 hour → tier 3. 10 seconds of continuous good posture resets
everything.

### 4. Audio

Plays a random WAV from the tier's `phrases/` directory. Falls back to the
macOS system beep if a directory is empty.

## Custom Voice Phrases

Drop your own `.wav` files into:

- `phrases/tier1_gentle/`
- `phrases/tier2_firm/`
- `phrases/tier3_nuclear/`

The app picks a random file from the matching tier. If all folders are empty,
it falls back to the macOS system beep.

### Phrase Presets (ElevenLabs)

`generate_voice.py` supports two phrase presets via `SLOUCHY_PHRASE_PRESET`:

- `english_fun`
- `hinglish_fun`

Example:

```bash
export ELEVENLABS_API_KEY="your_key"
export ELEVENLABS_VOICE_ID="your_voice_id"   # optional
export SLOUCHY_PHRASE_PRESET="hinglish_fun"
./venv/bin/python generate_voice.py
```

You can also pass the preset as the 3rd CLI argument:

```bash
./venv/bin/python generate_voice.py "$ELEVENLABS_API_KEY" "$ELEVENLABS_VOICE_ID" "english_fun"
```

Other generators are still available:
- `generate_voice_f5.py` (F5-TTS, local)

## Building a Standalone .app

Bundle as a native macOS app (no Python install needed to run):

```bash
make release
```

`make release` creates/uses an isolated `.venv-release` with
`requirements-release.txt`, so packaging stays deterministic and avoids
accidentally bundling extra dev dependencies.

Artifacts:
- `dist/Slouchy.app`
- `dist/Slouchy.dmg`

> **Note:** The `models/` directory and `phrases/` WAV files must be bundled.
> `setup.py` is pre-configured to include them.

## Development

```bash
# Run the full test suite
python -m pytest tests/ -v

# 77 tests across posture, escalation, audio, tracker, dashboard, and menubar modules
```







## Privacy

- Video frames are **never stored or transmitted**. Processing happens in-memory.
- The SQLite database (`~/.slouchy/posture.db`) stores only timestamps, durations,
  and tier numbers — no images, no landmarks, no personal data.
- Calibration data (`~/.slouchy/calibration.json`) contains only numeric
  baseline ratios — not biometric data.



## License

MIT
