#!/usr/bin/env bash
# Slouchy — one-command setup
# Usage: ./install.sh
set -euo pipefail

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║     Slouchy — Installing...        ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "  ✗ Slouchy only runs on macOS."
    exit 1
fi

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "  ✗ Python 3 not found. Install it:"
    echo "    brew install python@3.11"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 10)))')
if [[ "$PY_OK" != "1" ]]; then
    echo "  ✗ Python $PY_VERSION is too old. Slouchy requires Python 3.10+."
    echo "    Install a newer version:"
    echo "      brew install python@3.11"
    echo "    Then re-run: make install"
    exit 1
fi
echo "  ✓ Python $PY_VERSION found"

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "  → Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
echo "  ✓ Virtual environment activated"

# Install dependencies
echo "  → Installing dependencies..."
pip install -q -r requirements.txt
echo "  ✓ Dependencies installed"

# Pre-download MediaPipe model (optional — app auto-downloads on first launch)
MODEL_PATH="models/pose_landmarker_heavy.task"
if [ ! -f "$MODEL_PATH" ]; then
    echo "  → Pre-downloading pose model (29 MB) for faster first launch..."
    mkdir -p models
    curl -sL -o "$MODEL_PATH" \
        https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task
    echo "  ✓ Model downloaded"
else
    echo "  ✓ Model already exists"
fi

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║     ✓ Setup complete!                ║"
echo "  ║                                      ║"
echo "  ║  Run the app:                        ║"
echo "  ║    source venv/bin/activate           ║"
echo "  ║    python menubar.py                  ║"
echo "  ║                                      ║"
echo "  ║  Or just:  make run                   ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
