"""Audio playback manager for Slouchy posture correction alerts.

Uses PyObjC's NSSound for WAV playback on macOS with a graceful
no-op fallback for non-Mac platforms (Linux CI, etc.).

Playback follows a three-step fallback chain:
    1. Random WAV from the requested tier directory
    2. Random WAV from ANY tier directory that has files
    3. macOS system beep via NSBeep()
"""

import glob
import os
import random

NSSound = None

def NSBeep():
    """No-op fallback when AppKit beep is unavailable."""
    return None

try:
    from AppKit import NSSound, NSBeep
    HAS_APPKIT = True
except Exception:
    # Some environments (or repeated PyObjC loads in tests) can raise
    # non-ImportError exceptions here. Audio should fail closed, not crash.
    HAS_APPKIT = False

from config import PHRASES_DIR, TIER_DIRS


class AudioPlayer:
    """Manages posture-alert audio playback with tier-based escalation.

    Usage::

        player = AudioPlayer()
        filename = player.play_tier(1)  # gentle nudge
        filename = player.play_tier(3)  # nuclear
    """

    def __init__(self, phrases_dir: str | None = None):
        self._phrases_dir = phrases_dir if phrases_dir is not None else PHRASES_DIR
        self._current_sound = None  # NSSound instance or None
        # Track last played filename per tier to avoid immediate repetition.
        self._last_played: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def play_tier(self, tier: int) -> str | None:
        """Play a random WAV from *tier*'s directory.

        If currently playing, the active sound is interrupted first.

        Returns:
            The basename of the file played, or ``None`` if no WAV was
            available and the system beep was used instead.
        """
        if self.is_playing():
            self.stop()

        # --- Fallback chain ---

        # 1. Try the requested tier.
        phrases = self._get_phrases(tier)
        chosen = self._pick_random(phrases, tier)

        # 2. If empty, try any other tier that has files.
        if chosen is None:
            for other_tier in sorted(TIER_DIRS):
                if other_tier == tier:
                    continue
                phrases = self._get_phrases(other_tier)
                chosen = self._pick_random(phrases, other_tier)
                if chosen is not None:
                    break

        # 3. Last resort: system beep.
        if chosen is None:
            self._play_system_beep()
            return None

        self._play_file(chosen)
        basename = os.path.basename(chosen)
        # Record to avoid immediate repetition.
        tier_key = tier  # always keyed to the *requested* tier
        self._last_played[tier_key] = basename
        return basename

    def stop(self) -> None:
        """Stop the currently playing sound, if any."""
        if self._current_sound is not None:
            self._current_sound.stop()
            self._current_sound = None

    def is_playing(self) -> bool:
        """Return ``True`` if a sound is currently playing."""
        if self._current_sound is None:
            return False
        return bool(self._current_sound.isPlaying())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_phrases(self, tier: int) -> list[str]:
        """Return a list of WAV file paths for *tier*.

        Uses the tier directory mapping from config, overriding the base
        ``phrases_dir`` if one was passed to the constructor.
        """
        # Build directory path relative to the configured phrases dir.
        default_dir = TIER_DIRS.get(tier)
        if default_dir is None:
            return []

        # If a custom phrases_dir was provided, reconstruct the tier path
        # using the same subdirectory name.
        tier_subdir = os.path.basename(default_dir)
        tier_dir = os.path.join(self._phrases_dir, tier_subdir)

        if not os.path.isdir(tier_dir):
            return []

        return sorted(glob.glob(os.path.join(tier_dir, "*.wav")))

    def _pick_random(self, phrases: list[str], tier: int) -> str | None:
        """Pick a random phrase, avoiding the last-played file for *tier*."""
        if not phrases:
            return None

        last = self._last_played.get(tier)
        candidates = [p for p in phrases if os.path.basename(p) != last]
        # If only one file exists (or all filtered out), allow repeats.
        if not candidates:
            candidates = phrases

        return random.choice(candidates)

    def _play_file(self, filepath: str) -> None:
        """Create an NSSound from *filepath* and start playback."""
        if not HAS_APPKIT:
            return
        sound = NSSound.alloc().initWithContentsOfFile_byReference_(filepath, True)
        if sound is not None:
            sound.play()
            self._current_sound = sound

    def _play_system_beep(self) -> None:
        """Fallback: play the macOS system beep via NSBeep()."""
        if HAS_APPKIT:
            NSBeep()
