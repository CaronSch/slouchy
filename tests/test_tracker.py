"""Comprehensive tests for the PostureTracker SQLite module."""

import datetime
import os
import sqlite3
import sys
from unittest import mock
from unittest.mock import MagicMock

import pytest

# Ensure the project root is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tracker import PostureTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(date, hour=12, minute=0, second=0):
    """Return a unix timestamp for *date* at the given local time."""
    return datetime.datetime.combine(
        date, datetime.time(hour, minute, second)
    ).timestamp()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tracker():
    """In-memory tracker -- fast and isolated."""
    return PostureTracker(db_path=":memory:")


@pytest.fixture
def today():
    return datetime.date.today()


# ---------------------------------------------------------------------------
# 1. Insert slouch event and retrieve it
# ---------------------------------------------------------------------------

def test_log_and_retrieve_slouch_event(tracker, today):
    ts = _ts(today, 10, 30)
    row_id = tracker.log_slouch(timestamp=ts, duration=45.0, tier=1)

    assert row_id is not None

    events = tracker.get_all_events()
    assert len(events) == 1

    evt = events[0]
    assert evt["id"] == row_id
    assert evt["timestamp"] == ts
    assert evt["duration"] == 45.0
    assert evt["tier"] == 1


# ---------------------------------------------------------------------------
# 2. Daily stats: total slouch time computed correctly
# ---------------------------------------------------------------------------

def test_daily_stats_total_slouch_time(tracker, today):
    tracker.log_slouch(_ts(today, 9, 0), duration=30.0, tier=1)
    tracker.log_slouch(_ts(today, 10, 0), duration=60.0, tier=1)
    tracker.log_slouch(_ts(today, 14, 0), duration=15.5, tier=2)

    stats = tracker.get_daily_stats(today)

    assert stats["total_slouch_time"] == pytest.approx(105.5)
    assert stats["total_events"] == 3


# ---------------------------------------------------------------------------
# 3. Daily stats: longest streak identified correctly
# ---------------------------------------------------------------------------

def test_daily_stats_longest_streak(tracker, today):
    tracker.log_slouch(_ts(today, 8), duration=10.0, tier=1)
    tracker.log_slouch(_ts(today, 9), duration=120.0, tier=2)  # longest
    tracker.log_slouch(_ts(today, 10), duration=45.0, tier=1)

    stats = tracker.get_daily_stats(today)

    assert stats["longest_streak"] == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# 4. Daily stats: tier counts accurate
# ---------------------------------------------------------------------------

def test_daily_stats_tier_counts(tracker, today):
    tracker.log_slouch(_ts(today, 8), 10.0, tier=1)
    tracker.log_slouch(_ts(today, 9), 20.0, tier=1)
    tracker.log_slouch(_ts(today, 10), 60.0, tier=2)
    tracker.log_slouch(_ts(today, 11), 300.0, tier=3)
    tracker.log_slouch(_ts(today, 12), 15.0, tier=1)

    stats = tracker.get_daily_stats(today)

    assert stats["tier_counts"] == {1: 3, 2: 1, 3: 1}


# ---------------------------------------------------------------------------
# 5. DB file creation: creates directory and file on first run
# ---------------------------------------------------------------------------

def test_db_file_and_directory_creation(tmp_path):
    nested = tmp_path / "deep" / "nested" / "dir"
    db_file = nested / "test.db"

    assert not nested.exists()

    tracker = PostureTracker(db_path=str(db_file))

    assert nested.is_dir()
    assert db_file.is_file()

    # Verify tables exist by inserting and reading back.
    tracker.log_slouch(timestamp=1000.0, duration=5.0, tier=1)
    events = tracker.get_all_events()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# 6. SQLite write failure: graceful degradation
# ---------------------------------------------------------------------------

def test_sqlite_write_failure_graceful(tracker, today):
    # Seed one event so we can verify stats still return safe defaults
    tracker.log_slouch(_ts(today, 9), 10.0, tier=1)

    # Replace the connection with a mock that raises on execute
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = sqlite3.OperationalError("disk I/O error")
    mock_cursor.fetchone.side_effect = sqlite3.OperationalError("disk I/O error")
    mock_cursor.fetchall.side_effect = sqlite3.OperationalError("disk I/O error")
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.commit.side_effect = sqlite3.OperationalError("disk I/O error")
    tracker._conn = mock_conn

    # log_slouch should return None, not raise
    assert tracker.log_slouch(_ts(today, 10), 20.0, tier=2) is None

    # start_session should return None
    assert tracker.start_session() is None

    # end_session should return False
    assert tracker.end_session(1) is False

    # get_daily_stats should return safe empty defaults
    stats = tracker.get_daily_stats(today)
    assert stats["total_slouch_time"] == 0.0
    assert stats["total_events"] == 0
    assert stats["tier_counts"] == {1: 0, 2: 0, 3: 0}

    # get_all_events should return an empty list
    assert tracker.get_all_events() == []


# ---------------------------------------------------------------------------
# 7. Today summary: posture score and monitoring time
# ---------------------------------------------------------------------------

def test_today_summary_with_data(tracker, today):
    """get_today_summary returns correct aggregation of session and event data."""
    # Create a session
    sid = tracker.start_session()
    # Log some slouch events
    tracker.log_slouch(_ts(today, 10), duration=60.0, tier=1)
    tracker.log_slouch(_ts(today, 11), duration=30.0, tier=2)

    summary = tracker.get_today_summary(today)

    assert summary["total_slouch_minutes"] == pytest.approx(1.5)  # 90s = 1.5min
    assert summary["slouch_events"] == 2
    assert summary["posture_score"] <= 100.0


def test_today_summary_no_data(tracker, today):
    """get_today_summary returns safe defaults when no data exists."""
    summary = tracker.get_today_summary(today)

    assert summary["total_monitoring_minutes"] == 0.0
    assert summary["total_slouch_minutes"] == 0.0
    assert summary["slouch_events"] == 0
    assert summary["posture_score"] == 100.0


# ---------------------------------------------------------------------------
# 8. Good streak logging and retrieval
# ---------------------------------------------------------------------------

def test_log_good_streak(tracker, today):
    """log_good_streak inserts a record and it shows up in today_summary."""
    row_id = tracker.log_good_streak(_ts(today, 10), duration=1800.0)
    assert row_id is not None

    summary = tracker.get_today_summary(today)
    assert summary["best_streak_minutes"] == pytest.approx(30.0)  # 1800s = 30min


def test_log_multiple_streaks_returns_best(tracker, today):
    """Best streak is the max of all logged streaks."""
    tracker.log_good_streak(_ts(today, 9), duration=600.0)    # 10 min
    tracker.log_good_streak(_ts(today, 11), duration=3600.0)  # 60 min (best)
    tracker.log_good_streak(_ts(today, 14), duration=900.0)   # 15 min

    summary = tracker.get_today_summary(today)
    assert summary["best_streak_minutes"] == pytest.approx(60.0)


def test_log_good_streak_failure_returns_none(tracker, today):
    """Graceful failure when DB is broken."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = sqlite3.OperationalError("disk I/O error")
    mock_conn.cursor.return_value = mock_cursor
    tracker._conn = mock_conn

    assert tracker.log_good_streak(_ts(today, 10), 100.0) is None


def test_today_summary_counts_cross_midnight_session(tracker, today):
    """Monitoring total includes overlap from sessions started yesterday."""
    yesterday = today - datetime.timedelta(days=1)
    start = datetime.datetime.combine(yesterday, datetime.time(23, 30)).timestamp()
    end = datetime.datetime.combine(today, datetime.time(0, 30)).timestamp()

    cur = tracker._conn.cursor()
    cur.execute(
        "INSERT INTO sessions (start_time, end_time) VALUES (?, ?)",
        (start, end),
    )
    tracker._conn.commit()

    summary = tracker.get_today_summary(today)
    assert summary["total_monitoring_minutes"] == pytest.approx(30.0, abs=0.1)


def test_recent_days_summary_returns_requested_window(tracker, today):
    """Recent summary returns one row per day and includes slouch aggregates."""
    three_days_ago = today - datetime.timedelta(days=2)
    tracker.log_slouch(_ts(three_days_ago, 9), duration=120.0, tier=1)

    rows = tracker.get_recent_days_summary(days=3)
    assert len(rows) == 3
    assert rows[0]["date"] == three_days_ago.isoformat()
    assert rows[0]["slouch_seconds"] == pytest.approx(120.0)


def test_start_session_closes_stale_open_sessions(tracker):
    """Starting a new session closes any lingering open sessions first."""
    stale_start = 1000.0
    cur = tracker._conn.cursor()
    cur.execute("INSERT INTO sessions (start_time, end_time) VALUES (?, NULL)", (stale_start,))
    tracker._conn.commit()

    with mock.patch("tracker.time.time", return_value=2000.0), mock.patch(
        "tracker._SESSION_RECOVERY_MAX_SECONDS", 300.0
    ):
        new_id = tracker.start_session()

    assert new_id is not None

    cur.execute(
        "SELECT id, start_time, end_time FROM sessions ORDER BY id ASC"
    )
    rows = cur.fetchall()
    assert len(rows) == 2

    stale = rows[0]
    newest = rows[1]
    assert stale["end_time"] == pytest.approx(1300.0)
    assert newest["start_time"] == pytest.approx(2000.0)
    assert newest["end_time"] is None


def test_start_session_preserves_recent_open_session_duration(tracker):
    """Recently-open sessions are still closed at current time."""
    recent_start = 1900.0
    cur = tracker._conn.cursor()
    cur.execute("INSERT INTO sessions (start_time, end_time) VALUES (?, NULL)", (recent_start,))
    tracker._conn.commit()

    with mock.patch("tracker.time.time", return_value=2000.0), mock.patch(
        "tracker._SESSION_RECOVERY_MAX_SECONDS", 300.0
    ):
        tracker.start_session()

    cur.execute("SELECT start_time, end_time FROM sessions ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    assert row["start_time"] == pytest.approx(recent_start)
    assert row["end_time"] == pytest.approx(2000.0)
