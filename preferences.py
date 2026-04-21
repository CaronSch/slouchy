"""User preferences for Slouchy, persisted to ~/.slouchy/preferences.json."""

import json
import os
from dataclasses import asdict, dataclass

PREFERENCES_FILE = os.path.expanduser("~/.slouchy/preferences.json")


@dataclass
class Preferences:
    sound_enabled: bool = True
    text_notifications_enabled: bool = True
    active_hours_enabled: bool = False
    active_hours_start: int = 540   # 9:00 AM (minutes from midnight)
    active_hours_end: int = 1080    # 6:00 PM (minutes from midnight)


def load_preferences() -> Preferences:
    if os.path.exists(PREFERENCES_FILE):
        try:
            with open(PREFERENCES_FILE) as f:
                data = json.load(f)
            fields = Preferences.__dataclass_fields__
            return Preferences(**{k: v for k, v in data.items() if k in fields})
        except Exception:
            pass
    return Preferences()


def save_preferences(prefs: Preferences) -> None:
    os.makedirs(os.path.dirname(PREFERENCES_FILE), exist_ok=True)
    with open(PREFERENCES_FILE, "w") as f:
        json.dump(asdict(prefs), f, indent=2)


def minutes_to_timestr(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def timestr_to_minutes(s: str) -> int | None:
    """Parse 'HH:MM' to minutes from midnight, or None on bad input."""
    try:
        h, m = s.strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return None


def in_active_hours(current_minutes: int, start: int, end: int) -> bool:
    """Return True if current_minutes falls within [start, end). Handles overnight ranges."""
    if start == end:
        return True
    if start < end:
        return start <= current_minutes < end
    # Overnight range (e.g. 22:00 – 06:00)
    return current_minutes >= start or current_minutes < end
