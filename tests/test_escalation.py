"""Comprehensive tests for the Slouchy escalation state machine.

All 15 tests inject explicit monotonic-clock values so behaviour is
fully deterministic — no sleeps, no flaky timing.
"""

import sys
import os

# Ensure the project root is on the import path so `config` and
# `escalation` can be imported without package install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

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
from escalation import EscalationEngine, State


@pytest.fixture
def engine():
    return EscalationEngine()


# ------------------------------------------------------------------
# 1. GOOD -> SLOUCHING on slouch detected
# ------------------------------------------------------------------
def test_good_to_slouching(engine):
    t = 1000.0
    slouch_start = t
    action = engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    assert engine.state == State.SLOUCHING
    assert action is None  # no audio yet


# ------------------------------------------------------------------
# 2. SLOUCHING -> GOOD when posture fixed within 30s
# ------------------------------------------------------------------
def test_slouching_to_good_quick_fix(engine):
    t = 1000.0
    # Enter SLOUCHING
    engine.update(is_slouching=True, slouch_start_time=t, current_time=t)
    assert engine.state == State.SLOUCHING

    # Fix posture after 10s (well within 30s threshold)
    action = engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 10)
    assert engine.state == State.GOOD
    assert action is None


# ------------------------------------------------------------------
# 3. SLOUCHING -> TIER1 after 30s continuous slouch (returns tier=1)
# ------------------------------------------------------------------
def test_slouching_to_tier1(engine):
    t = 1000.0
    slouch_start = t
    # Enter SLOUCHING
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)

    # Still slouching at exactly 30s — should escalate.
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    assert engine.state == State.TIER1
    assert action == 1


# ------------------------------------------------------------------
# 4. TIER1 -> GOOD after 10+ seconds good posture (hard reset)
# ------------------------------------------------------------------
def test_tier1_hard_reset(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER1
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    assert engine.state == State.TIER1

    # Good posture begins
    engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 40)
    assert engine.state == State.TIER1  # not reset yet

    # 10s of good posture elapsed
    engine.update(
        is_slouching=False,
        slouch_start_time=None,
        current_time=t + 40 + GOOD_POSTURE_RESET_SECONDS,
    )
    assert engine.state == State.GOOD


# ------------------------------------------------------------------
# 5. TIER1 cooldown: no repeat audio within 5 minutes
# ------------------------------------------------------------------
def test_tier1_cooldown(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER1 (fires tier=1)
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    assert action == 1

    # Brief good posture (not enough to reset) then slouch again.
    engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 35)
    engine.update(is_slouching=True, slouch_start_time=t + 40, current_time=t + 40)

    # Still within cooldown — should NOT re-trigger.
    action = engine.update(
        is_slouching=True,
        slouch_start_time=t + 40,
        current_time=t + 40 + SLOUCH_TIER1_SECONDS,
    )
    # Under the 5-min cooldown window, no new audio.
    assert action is None
    assert engine.state == State.TIER1


# ------------------------------------------------------------------
# 6. TIER1 -> TIER2 after 2min continuous slouch
# ------------------------------------------------------------------
def test_tier1_to_tier2_duration(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER1
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    assert engine.state == State.TIER1

    # Slouch continues to 2 min — should escalate to TIER2.
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER2_SECONDS,
    )
    assert engine.state == State.TIER2
    assert action == 2


# ------------------------------------------------------------------
# 7. TIER1 -> TIER2 on 3rd gentle trigger in 30min window
# ------------------------------------------------------------------
def test_tier1_to_tier2_frequency(engine):
    t = 1000.0

    def slouch_and_trigger(start, trigger_at):
        """Simulate a slouch episode that reaches tier1."""
        engine.update(is_slouching=True, slouch_start_time=start, current_time=start)
        return engine.update(
            is_slouching=True, slouch_start_time=start, current_time=trigger_at
        )

    def hard_reset_at(good_start):
        """Simulate good posture long enough to hard-reset."""
        engine.update(is_slouching=False, slouch_start_time=None, current_time=good_start)
        engine.update(
            is_slouching=False,
            slouch_start_time=None,
            current_time=good_start + GOOD_POSTURE_RESET_SECONDS,
        )
        assert engine.state == State.GOOD

    # -- 1st gentle trigger --
    action = slouch_and_trigger(t, t + SLOUCH_TIER1_SECONDS)
    assert action == 1
    assert engine.state == State.TIER1
    hard_reset_at(t + 35)

    # -- 2nd gentle trigger (after cooldown) --
    t2 = t + TIER1_COOLDOWN_SECONDS + 50
    engine.update(is_slouching=True, slouch_start_time=t2, current_time=t2)
    action = engine.update(
        is_slouching=True,
        slouch_start_time=t2,
        current_time=t2 + SLOUCH_TIER1_SECONDS,
    )
    assert action == 1
    assert engine.state == State.TIER1
    hard_reset_at(t2 + 35)

    # -- 3rd gentle trigger — should escalate to TIER2 --
    t3 = t2 + TIER1_COOLDOWN_SECONDS + 50
    # All three triggers must be within the 30-min window.
    assert (t3 - t) < TIER1_FREQUENCY_WINDOW
    engine.update(is_slouching=True, slouch_start_time=t3, current_time=t3)
    action = engine.update(
        is_slouching=True,
        slouch_start_time=t3,
        current_time=t3 + SLOUCH_TIER1_SECONDS,
    )
    # First it enters tier1 (which records the 3rd trigger), then on the
    # next update the frequency check promotes to tier2.
    if engine.state == State.TIER1:
        # The 3rd trigger was recorded; next update should see the frequency.
        action = engine.update(
            is_slouching=True,
            slouch_start_time=t3,
            current_time=t3 + SLOUCH_TIER1_SECONDS + 1,
        )
    assert engine.state == State.TIER2
    assert action == 2


# ------------------------------------------------------------------
# 8. TIER2 -> GOOD after 10+ seconds good posture
# ------------------------------------------------------------------
def test_tier2_hard_reset(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER2 via duration
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER2_SECONDS,
    )
    assert engine.state == State.TIER2

    # Good posture for 10s
    engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 130)
    engine.update(
        is_slouching=False,
        slouch_start_time=None,
        current_time=t + 130 + GOOD_POSTURE_RESET_SECONDS,
    )
    assert engine.state == State.GOOD


# ------------------------------------------------------------------
# 9. TIER2 cooldown: no repeat within 10 minutes
# ------------------------------------------------------------------
def test_tier2_cooldown(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER2
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER2_SECONDS,
    )
    assert action == 2

    # Short good posture then slouch again — still within cooldown.
    engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 125)
    new_slouch = t + 130
    engine.update(is_slouching=True, slouch_start_time=new_slouch, current_time=new_slouch)

    # Within 10 min cooldown — no re-trigger.
    action = engine.update(
        is_slouching=True,
        slouch_start_time=new_slouch,
        current_time=new_slouch + 60,
    )
    assert action is None
    assert engine.state == State.TIER2


# ------------------------------------------------------------------
# 10. TIER2 -> TIER3 after 5min continuous slouch
# ------------------------------------------------------------------
def test_tier2_to_tier3_duration(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER2
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER2_SECONDS,
    )
    assert engine.state == State.TIER2

    # 5 min total slouch
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER3_SECONDS,
    )
    assert engine.state == State.TIER3
    assert action == 3


# ------------------------------------------------------------------
# 11. TIER2 -> TIER3 on 2nd firm trigger in 1 hour
# ------------------------------------------------------------------
def test_tier2_to_tier3_frequency(engine):
    t = 1000.0
    slouch_start = t
    # 1st firm trigger — drive to TIER2
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER2_SECONDS,
    )
    assert action == 2
    assert engine.state == State.TIER2

    # Hard reset
    engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 130)
    engine.update(
        is_slouching=False,
        slouch_start_time=None,
        current_time=t + 130 + GOOD_POSTURE_RESET_SECONDS,
    )
    assert engine.state == State.GOOD

    # 2nd slouch episode — drive back to TIER1 then TIER2 within the 1hr window.
    t2 = t + TIER2_COOLDOWN_SECONDS + 200
    assert (t2 - t) < TIER2_FREQUENCY_WINDOW  # within 1 hour

    slouch_start2 = t2
    engine.update(is_slouching=True, slouch_start_time=slouch_start2, current_time=t2)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start2,
        current_time=t2 + SLOUCH_TIER1_SECONDS,
    )
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start2,
        current_time=t2 + SLOUCH_TIER2_SECONDS,
    )
    # The 2nd firm trigger within the window should jump to TIER3.
    if engine.state == State.TIER2:
        # Frequency check fires on next update in TIER2.
        action = engine.update(
            is_slouching=True,
            slouch_start_time=slouch_start2,
            current_time=t2 + SLOUCH_TIER2_SECONDS + 1,
        )
    assert engine.state == State.TIER3
    assert action == 3


# ------------------------------------------------------------------
# 12. TIER3 cooldown: no repeat within 20 minutes
# ------------------------------------------------------------------
def test_tier3_cooldown(engine):
    t = 1000.0
    slouch_start = t
    # Drive all the way to TIER3 via duration
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER2_SECONDS,
    )
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER3_SECONDS,
    )
    assert action == 3

    # Within 20 min cooldown — no re-trigger.
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER3_SECONDS + TIER3_COOLDOWN_SECONDS - 1,
    )
    assert action is None
    assert engine.state == State.TIER3

    # After cooldown expires — re-trigger allowed.
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER3_SECONDS + TIER3_COOLDOWN_SECONDS,
    )
    assert action == 3


# ------------------------------------------------------------------
# 13. TIER3 -> GOOD after hard reset
# ------------------------------------------------------------------
def test_tier3_hard_reset(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER3
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER2_SECONDS,
    )
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER3_SECONDS,
    )
    assert engine.state == State.TIER3

    # 10s good posture — hard reset
    good_start = t + SLOUCH_TIER3_SECONDS + 5
    engine.update(is_slouching=False, slouch_start_time=None, current_time=good_start)
    engine.update(
        is_slouching=False,
        slouch_start_time=None,
        current_time=good_start + GOOD_POSTURE_RESET_SECONDS,
    )
    assert engine.state == State.GOOD


# ------------------------------------------------------------------
# 14. Brief posture fix (<10s) does NOT reset tier
# ------------------------------------------------------------------
def test_brief_fix_no_reset(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER1
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    assert engine.state == State.TIER1

    # Good posture for only 5s (less than 10s threshold)
    engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 35)
    engine.update(is_slouching=False, slouch_start_time=None, current_time=t + 35 + 5)
    assert engine.state == State.TIER1  # NOT reset

    # Resume slouching — still TIER1
    engine.update(is_slouching=True, slouch_start_time=t + 41, current_time=t + 41)
    assert engine.state == State.TIER1


# ------------------------------------------------------------------
# 15. Cannot skip from TIER1 directly to TIER3
# ------------------------------------------------------------------
def test_no_tier_skip(engine):
    t = 1000.0
    slouch_start = t
    # Drive to TIER1
    engine.update(is_slouching=True, slouch_start_time=slouch_start, current_time=t)
    engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER1_SECONDS,
    )
    assert engine.state == State.TIER1

    # Even if we report a massive slouch duration, TIER1 can only go to TIER2.
    action = engine.update(
        is_slouching=True,
        slouch_start_time=slouch_start,
        current_time=t + SLOUCH_TIER3_SECONDS,  # 5 min!
    )
    assert engine.state == State.TIER2
    assert action == 2
    # Confirm it is NOT TIER3.
    assert engine.state != State.TIER3
