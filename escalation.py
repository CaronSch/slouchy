"""Escalation state machine for Slouchy posture correction.

States: GOOD, SLOUCHING, TIER1, TIER2, TIER3

The engine receives posture data from the daemon thread and decides
when to trigger audio alerts at each tier. All timing uses monotonic
clock values passed in via update() for testability.
"""

import time
from enum import Enum, auto

from config import (
    GOOD_POSTURE_RESET_SECONDS,
    SLOUCH_TIER1_SECONDS,
    SLOUCH_TIER2_SECONDS,
    SLOUCH_TIER3_SECONDS,
    TIER1_COOLDOWN_SECONDS,
    TIER1_FREQUENCY_LIMIT,
    TIER1_FREQUENCY_WINDOW,
    TIER2_COOLDOWN_SECONDS,
    TIER2_FREQUENCY_LIMIT,
    TIER2_FREQUENCY_WINDOW,
    TIER3_COOLDOWN_SECONDS,
)


class State(Enum):
    GOOD = auto()
    SLOUCHING = auto()
    TIER1 = auto()
    TIER2 = auto()
    TIER3 = auto()


# Maps each tier state to its cooldown duration in seconds.
_COOLDOWNS = {
    State.TIER1: TIER1_COOLDOWN_SECONDS,
    State.TIER2: TIER2_COOLDOWN_SECONDS,
    State.TIER3: TIER3_COOLDOWN_SECONDS,
}


class EscalationEngine:
    """Tracks posture escalation state and decides when to fire audio alerts.

    Usage::

        engine = EscalationEngine()
        # Called every poll cycle by the daemon:
        action = engine.update(is_slouching, slouch_start_time, time.monotonic())
        if action is not None:
            play_audio(tier=action)
    """

    def __init__(self):
        self.state: State = State.GOOD

        # Monotonic timestamps of past tier triggers, used for
        # frequency-based escalation checks.
        self._tier1_triggers: list[float] = []  # gentle triggers
        self._tier2_triggers: list[float] = []  # firm triggers

        # Timestamp of last audio trigger per tier, for cooldown enforcement.
        self._last_trigger_time: dict[State, float | None] = {
            State.TIER1: None,
            State.TIER2: None,
            State.TIER3: None,
        }

        # Tracks continuous good posture for hard-reset detection.
        self._good_posture_start: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        is_slouching: bool,
        slouch_start_time: float | None,
        current_time: float | None = None,
    ) -> int | None:
        """Process a posture update and return an action.

        Args:
            is_slouching: Whether the user is currently slouching.
            slouch_start_time: Monotonic timestamp when the current slouch
                began, or ``None`` if not slouching.
            current_time: Monotonic timestamp for "now". Defaults to
                ``time.monotonic()`` in production; pass explicitly in tests.

        Returns:
            ``None`` if no action is needed, or a tier number (1, 2, 3)
            indicating which audio tier should be triggered.
        """
        if current_time is None:
            current_time = time.monotonic()

        if is_slouching:
            self._good_posture_start = None
            return self._handle_slouching(slouch_start_time, current_time)
        else:
            return self._handle_good_posture(current_time)

    # ------------------------------------------------------------------
    # Internal — slouching path
    # ------------------------------------------------------------------

    def _handle_slouching(
        self, slouch_start_time: float | None, now: float
    ) -> int | None:
        slouch_duration = (now - slouch_start_time) if slouch_start_time is not None else 0.0

        if self.state == State.GOOD:
            self.state = State.SLOUCHING
            return None

        if self.state == State.SLOUCHING:
            if slouch_duration >= SLOUCH_TIER1_SECONDS:
                return self._enter_tier(State.TIER1, now)
            return None

        if self.state == State.TIER1:
            # Duration-based escalation: 2 min continuous slouch.
            if slouch_duration >= SLOUCH_TIER2_SECONDS:
                return self._enter_tier(State.TIER2, now)
            # Frequency-based escalation: 3rd gentle trigger in 30 min.
            if self._check_frequency_escalation(
                self._tier1_triggers, TIER1_FREQUENCY_LIMIT, TIER1_FREQUENCY_WINDOW, now
            ):
                return self._enter_tier(State.TIER2, now)
            # Re-trigger tier 1 audio if cooldown has elapsed.
            return self._retrigger_if_ready(State.TIER1, now)

        if self.state == State.TIER2:
            # Duration-based escalation: 5 min continuous slouch.
            if slouch_duration >= SLOUCH_TIER3_SECONDS:
                return self._enter_tier(State.TIER3, now)
            # Frequency-based escalation: 2nd firm trigger in 1 hour.
            if self._check_frequency_escalation(
                self._tier2_triggers, TIER2_FREQUENCY_LIMIT, TIER2_FREQUENCY_WINDOW, now
            ):
                return self._enter_tier(State.TIER3, now)
            # Re-trigger tier 2 audio if cooldown has elapsed.
            return self._retrigger_if_ready(State.TIER2, now)

        if self.state == State.TIER3:
            return self._retrigger_if_ready(State.TIER3, now)

        return None

    # ------------------------------------------------------------------
    # Internal — good posture path
    # ------------------------------------------------------------------

    def _handle_good_posture(self, now: float) -> int | None:
        if self.state == State.GOOD:
            return None

        if self.state == State.SLOUCHING:
            # Quick fix before any tier was reached — go straight to GOOD.
            self.state = State.GOOD
            self._good_posture_start = None
            return None

        # In a tier — need 10+ continuous seconds of good posture to reset.
        if self._good_posture_start is None:
            self._good_posture_start = now

        elapsed_good = now - self._good_posture_start
        if elapsed_good >= GOOD_POSTURE_RESET_SECONDS:
            self._hard_reset()
        return None

    def _hard_reset(self) -> None:
        """Full reset back to GOOD after sustained good posture."""
        self.state = State.GOOD
        self._good_posture_start = None

    # ------------------------------------------------------------------
    # Internal — tier entry / cooldown helpers
    # ------------------------------------------------------------------

    def _enter_tier(self, tier: State, now: float) -> int | None:
        self.state = tier
        tier_num = {State.TIER1: 1, State.TIER2: 2, State.TIER3: 3}[tier]

        # Record in trigger history for frequency checks.
        if tier == State.TIER1:
            self._tier1_triggers.append(now)
        elif tier == State.TIER2:
            self._tier2_triggers.append(now)

        # Fire audio and record the trigger time.
        self._last_trigger_time[tier] = now
        return tier_num

    def _retrigger_if_ready(self, tier: State, now: float) -> int | None:
        """Re-fire audio for the current tier if cooldown has elapsed."""
        last = self._last_trigger_time.get(tier)
        cooldown = _COOLDOWNS[tier]
        if last is None or (now - last) >= cooldown:
            tier_num = {State.TIER1: 1, State.TIER2: 2, State.TIER3: 3}[tier]
            self._last_trigger_time[tier] = now
            # Record re-triggers in history too for frequency escalation.
            if tier == State.TIER1:
                self._tier1_triggers.append(now)
            elif tier == State.TIER2:
                self._tier2_triggers.append(now)
            return tier_num
        return None

    @staticmethod
    def _check_frequency_escalation(
        triggers: list[float], limit: int, window: float, now: float
    ) -> bool:
        """Return True if we've hit `limit` triggers within `window` seconds."""
        recent = [t for t in triggers if (now - t) <= window]
        return len(recent) >= limit
