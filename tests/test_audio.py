"""Tests for Slouchy audio playback manager.

All tests mock NSSound so no actual audio is played.  Temporary
directories with dummy .wav files stand in for the real phrase dirs.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers — temporary phrase directory tree
# ---------------------------------------------------------------------------

def _make_phrase_dirs(tmp_path, tiers=None):
    """Create tier subdirectories with dummy .wav files.

    Args:
        tmp_path: pytest tmp_path fixture value.
        tiers: dict mapping tier number -> list of filenames to create.
            Defaults to a reasonable set for all three tiers.

    Returns:
        The root phrases directory path (str).
    """
    if tiers is None:
        tiers = {
            1: ["gentle1.wav", "gentle2.wav"],
            2: ["firm1.wav"],
            3: ["nuclear1.wav", "nuclear2.wav", "nuclear3.wav"],
        }

    subdir_names = {1: "tier1_gentle", 2: "tier2_firm", 3: "tier3_nuclear"}
    for tier_num, filenames in tiers.items():
        tier_dir = tmp_path / subdir_names[tier_num]
        tier_dir.mkdir(parents=True, exist_ok=True)
        for fname in filenames:
            (tier_dir / fname).write_bytes(b"\x00" * 16)

    # Ensure all three subdirectories exist even if empty.
    for name in subdir_names.values():
        (tmp_path / name).mkdir(exist_ok=True)

    return str(tmp_path)


def _make_mock_sound():
    """Return a MagicMock that behaves like an NSSound instance."""
    mock_sound = MagicMock()
    mock_sound.isPlaying.return_value = False
    mock_sound.play.return_value = True
    mock_sound.stop.return_value = None
    return mock_sound


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def phrases_dir(tmp_path):
    """Standard phrase directory tree with files in all three tiers."""
    return _make_phrase_dirs(tmp_path)


@pytest.fixture()
def empty_phrases_dir(tmp_path):
    """Phrase directory tree where every tier subdirectory is empty."""
    return _make_phrase_dirs(tmp_path, tiers={1: [], 2: [], 3: []})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlayTierSelectsCorrectTier:
    """play_tier() picks a WAV from the correct tier directory."""

    def test_selects_from_requested_tier(self, phrases_dir):
        mock_sound = _make_mock_sound()

        with patch("audio.HAS_APPKIT", True), \
             patch("audio.NSSound") as MockNSSound:
            MockNSSound.alloc.return_value.initWithContentsOfFile_byReference_.return_value = mock_sound

            from audio import AudioPlayer
            player = AudioPlayer(phrases_dir=phrases_dir)
            result = player.play_tier(1)

        assert result is not None
        assert result.startswith("gentle")
        assert result.endswith(".wav")

    def test_selects_from_tier3(self, phrases_dir):
        mock_sound = _make_mock_sound()

        with patch("audio.HAS_APPKIT", True), \
             patch("audio.NSSound") as MockNSSound:
            MockNSSound.alloc.return_value.initWithContentsOfFile_byReference_.return_value = mock_sound

            from audio import AudioPlayer
            player = AudioPlayer(phrases_dir=phrases_dir)
            result = player.play_tier(3)

        assert result is not None
        assert result.startswith("nuclear")


class TestPlayTierInterruptsBehavior:
    """play_tier() stops current audio before starting new playback."""

    def test_interrupts_current_audio(self, phrases_dir):
        first_sound = _make_mock_sound()
        first_sound.isPlaying.return_value = True
        second_sound = _make_mock_sound()

        with patch("audio.HAS_APPKIT", True), \
             patch("audio.NSSound") as MockNSSound:
            MockNSSound.alloc.return_value.initWithContentsOfFile_byReference_.side_effect = [
                first_sound,
                second_sound,
            ]

            from audio import AudioPlayer
            player = AudioPlayer(phrases_dir=phrases_dir)

            # First play.
            player.play_tier(1)
            assert player._current_sound is first_sound

            # Second play while first is still "playing".
            player.play_tier(2)

            # The first sound must have been stopped before the second started.
            first_sound.stop.assert_called_once()
            second_sound.play.assert_called_once()


class TestFallbackToOtherTier:
    """When the requested tier is empty, fall back to another tier."""

    def test_empty_tier_falls_back(self, tmp_path):
        # Only tier 3 has files.
        phrases_dir = _make_phrase_dirs(tmp_path, tiers={1: [], 2: [], 3: ["backup.wav"]})
        mock_sound = _make_mock_sound()

        with patch("audio.HAS_APPKIT", True), \
             patch("audio.NSSound") as MockNSSound:
            MockNSSound.alloc.return_value.initWithContentsOfFile_byReference_.return_value = mock_sound

            from audio import AudioPlayer
            player = AudioPlayer(phrases_dir=phrases_dir)
            result = player.play_tier(1)

        # Should have fallen back to tier 3's file.
        assert result == "backup.wav"


class TestFallbackToSystemBeep:
    """When ALL tier directories are empty, fall back to system beep."""

    def test_all_empty_plays_beep(self, empty_phrases_dir):
        with patch("audio.HAS_APPKIT", True), \
             patch("audio.NSBeep") as mock_beep:
            from audio import AudioPlayer
            player = AudioPlayer(phrases_dir=empty_phrases_dir)
            result = player.play_tier(1)

        assert result is None
        mock_beep.assert_called_once()


class TestMissingPhrasesDirectory:
    """No crash when the phrases directory doesn't exist at all."""

    def test_nonexistent_dir_returns_none(self, tmp_path):
        bogus_dir = str(tmp_path / "does_not_exist")

        with patch("audio.HAS_APPKIT", True), \
             patch("audio.NSBeep") as mock_beep:
            from audio import AudioPlayer
            player = AudioPlayer(phrases_dir=bogus_dir)
            result = player.play_tier(2)

        # Should fall through every tier, find nothing, beep, and return None.
        assert result is None
        mock_beep.assert_called_once()
