"""SQLite-backed posture tracking: slouch events, sessions, and daily stats."""

import os
import sqlite3
import sys
import time
import datetime

from config import DB_PATH

_SESSION_RECOVERY_MAX_SECONDS = 5 * 60

_SCHEMA_SLOUCH_EVENTS = """
CREATE TABLE IF NOT EXISTS slouch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    duration REAL NOT NULL,
    tier INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SCHEMA_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SCHEMA_ABSENCE_PERIODS = """
CREATE TABLE IF NOT EXISTS absence_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class PostureTracker:
    """Records slouch events and sessions in a local SQLite database."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._ensure_directory()
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    # ---- internal helpers ------------------------------------------------

    def _ensure_directory(self):
        """Create the parent directory for the DB file if it doesn't exist.

        The special value ':memory:' is an in-memory database and needs no
        directory.
        """
        if self.db_path == ":memory:":
            return
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _create_tables(self):
        cur = self._conn.cursor()
        cur.executescript(_SCHEMA_SLOUCH_EVENTS + _SCHEMA_SESSIONS + _SCHEMA_ABSENCE_PERIODS)
        self._conn.commit()

    def _compute_monitoring_seconds_by_day(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> dict[str, float]:
        """Return per-day monitoring seconds for sessions overlapping [start_date, end_date].

        Sessions are split across day boundaries so cross-midnight tracking is
        attributed to the correct local day.
        """
        start_ts = datetime.datetime.combine(start_date, datetime.time.min).timestamp()
        end_ts = datetime.datetime.combine(end_date, datetime.time.max).timestamp()

        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT start_time, COALESCE(end_time, ?) AS end_time
                FROM sessions
                WHERE start_time <= ? AND COALESCE(end_time, ?) >= ?
                """,
                (time.time(), end_ts, time.time(), start_ts),
            )
            sessions = cur.fetchall()
        except sqlite3.Error as exc:
            print(f"[tracker] failed to query sessions for overlap: {exc}", file=sys.stderr)
            return {}

        totals: dict[str, float] = {}
        day = start_date
        one_day = datetime.timedelta(days=1)
        while day <= end_date:
            day_key = day.isoformat()
            day_start = datetime.datetime.combine(day, datetime.time.min).timestamp()
            day_end = datetime.datetime.combine(day, datetime.time.max).timestamp()
            total = 0.0
            for row in sessions:
                s = float(row["start_time"])
                e = float(row["end_time"])
                overlap_start = max(s, day_start)
                overlap_end = min(e, day_end)
                if overlap_end > overlap_start:
                    total += overlap_end - overlap_start
            totals[day_key] = total
            day += one_day
        return totals

    # ---- public API ------------------------------------------------------

    def log_slouch(self, timestamp, duration, tier):
        """Insert a slouch event. Returns the new row id, or None on failure."""
        try:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO slouch_events (timestamp, duration, tier) VALUES (?, ?, ?)",
                (timestamp, duration, tier),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as exc:
            print(f"[tracker] failed to log slouch event: {exc}", file=sys.stderr)
            return None

    def start_session(self):
        """Record a new session start. Returns the session id, or None on failure."""
        try:
            now = time.time()
            cur = self._conn.cursor()
            # Close stale open sessions first (e.g. app was force-quit).
            # Cap recovered open-session duration to avoid counting long downtime.
            cur.execute(
                """
                UPDATE sessions
                SET end_time = MIN(?, start_time + ?)
                WHERE end_time IS NULL
                """,
                (now, _SESSION_RECOVERY_MAX_SECONDS),
            )
            cur.execute(
                "INSERT INTO sessions (start_time) VALUES (?)",
                (now,),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as exc:
            print(f"[tracker] failed to start session: {exc}", file=sys.stderr)
            return None

    def end_session(self, session_id):
        """Record session end time. Returns True on success, False on failure."""
        try:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE sessions SET end_time = ? WHERE id = ?",
                (time.time(), session_id),
            )
            self._conn.commit()
            return True
        except sqlite3.Error as exc:
            print(f"[tracker] failed to end session {session_id}: {exc}", file=sys.stderr)
            return False

    def get_daily_stats(self, date=None):
        """Return aggregate stats for a single calendar day.

        Parameters
        ----------
        date : datetime.date | None
            The date to query. Defaults to today (local time).

        Returns
        -------
        dict with keys:
            total_slouch_time  - sum of durations in seconds
            longest_streak     - max single-event duration in seconds
            tier_counts        - {1: int, 2: int, 3: int}
            total_events       - count of slouch events that day
        """
        import datetime

        if date is None:
            date = datetime.date.today()

        # Build unix-timestamp bounds for the local calendar day.
        day_start = datetime.datetime.combine(date, datetime.time.min).timestamp()
        day_end = datetime.datetime.combine(date, datetime.time.max).timestamp()

        try:
            cur = self._conn.cursor()

            # Aggregate query
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(duration), 0.0)  AS total_slouch_time,
                    COALESCE(MAX(duration), 0.0)  AS longest_streak,
                    COUNT(*)                       AS total_events
                FROM slouch_events
                WHERE timestamp >= ? AND timestamp <= ?
                """,
                (day_start, day_end),
            )
            row = cur.fetchone()

            # Tier breakdown
            cur.execute(
                """
                SELECT tier, COUNT(*) AS cnt
                FROM slouch_events
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY tier
                """,
                (day_start, day_end),
            )
            tier_counts = {1: 0, 2: 0, 3: 0}
            for tier_row in cur.fetchall():
                tier_counts[tier_row["tier"]] = tier_row["cnt"]

            return {
                "total_slouch_time": row["total_slouch_time"],
                "longest_streak": row["longest_streak"],
                "tier_counts": tier_counts,
                "total_events": row["total_events"],
            }
        except sqlite3.Error as exc:
            print(f"[tracker] failed to query daily stats: {exc}", file=sys.stderr)
            return {
                "total_slouch_time": 0.0,
                "longest_streak": 0.0,
                "tier_counts": {1: 0, 2: 0, 3: 0},
                "total_events": 0,
            }

    def get_all_events(self, limit=100):
        """Return the most recent slouch events as a list of dicts."""
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, timestamp, duration, tier, created_at
                FROM slouch_events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.Error as exc:
            print(f"[tracker] failed to fetch events: {exc}", file=sys.stderr)
            return []

    def get_today_summary(self, date=None):
        """Return a compact summary for menu display.

        Returns
        -------
        dict with keys:
            total_monitoring_minutes - total session time today in minutes
            total_slouch_minutes     - sum of slouch durations in minutes
            slouch_events            - count of slouch events
            best_streak_minutes      - longest good-posture streak in minutes
            posture_score            - percentage of good posture time (0-100)
        """
        if date is None:
            date = datetime.date.today()

        day_start = datetime.datetime.combine(date, datetime.time.min).timestamp()
        day_end = datetime.datetime.combine(date, datetime.time.max).timestamp()

        try:
            cur = self._conn.cursor()

            # Total monitoring time, correctly handling sessions spanning midnight.
            total_monitoring = self._compute_monitoring_seconds_by_day(date, date).get(
                date.isoformat(), 0.0
            )

            # Slouch stats
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(duration), 0.0) AS total_slouch,
                    COALESCE(MAX(duration), 0.0) AS longest_slouch,
                    COUNT(*) AS event_count
                FROM slouch_events
                WHERE timestamp >= ? AND timestamp <= ?
                """,
                (day_start, day_end),
            )
            row = cur.fetchone()
            total_slouch = row["total_slouch"]
            event_count = row["event_count"]

            # Best streak from good_streaks table (if it exists)
            best_streak = 0.0
            try:
                cur.execute(
                    """
                    SELECT COALESCE(MAX(duration), 0.0) AS best
                    FROM good_streaks
                    WHERE timestamp >= ? AND timestamp <= ?
                    """,
                    (day_start, day_end),
                )
                best_streak = cur.fetchone()["best"]
            except sqlite3.OperationalError:
                pass  # table doesn't exist yet

            # Posture score: % of monitoring time with good posture
            posture_score = 100.0
            if total_monitoring > 0:
                good_time = max(0, total_monitoring - total_slouch)
                posture_score = min(100.0, (good_time / total_monitoring) * 100)

            return {
                "total_monitoring_minutes": total_monitoring / 60,
                "total_slouch_minutes": total_slouch / 60,
                "slouch_events": event_count,
                "best_streak_minutes": best_streak / 60,
                "posture_score": round(posture_score, 1),
            }
        except sqlite3.Error as exc:
            print(f"[tracker] failed to query today summary: {exc}", file=sys.stderr)
            return {
                "total_monitoring_minutes": 0.0,
                "total_slouch_minutes": 0.0,
                "slouch_events": 0,
                "best_streak_minutes": 0.0,
                "posture_score": 100.0,
            }

    def get_recent_days_summary(self, days: int = 14):
        """Return daily aggregates for the last `days` local dates (oldest -> newest)."""
        if days < 1:
            return []

        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=days - 1)
        start_ts = datetime.datetime.combine(start_date, datetime.time.min).timestamp()
        end_ts = datetime.datetime.combine(end_date, datetime.time.max).timestamp()

        monitoring_by_day = self._compute_monitoring_seconds_by_day(start_date, end_date)
        slouch_by_day: dict[str, dict[str, float]] = {}

        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT
                    date(timestamp, 'unixepoch', 'localtime') AS day,
                    COALESCE(SUM(duration), 0.0) AS slouch_seconds,
                    COUNT(*) AS event_count
                FROM slouch_events
                WHERE timestamp >= ? AND timestamp <= ?
                GROUP BY day
                """,
                (start_ts, end_ts),
            )
            for row in cur.fetchall():
                slouch_by_day[row["day"]] = {
                    "slouch_seconds": float(row["slouch_seconds"]),
                    "event_count": int(row["event_count"]),
                }
        except sqlite3.Error as exc:
            print(f"[tracker] failed to query recent day summary: {exc}", file=sys.stderr)

        result = []
        day = start_date
        one_day = datetime.timedelta(days=1)
        while day <= end_date:
            day_key = day.isoformat()
            monitoring_seconds = monitoring_by_day.get(day_key, 0.0)
            slouch_seconds = slouch_by_day.get(day_key, {}).get("slouch_seconds", 0.0)
            event_count = slouch_by_day.get(day_key, {}).get("event_count", 0)
            posture_score = 100.0
            if monitoring_seconds > 0:
                good_seconds = max(0.0, monitoring_seconds - slouch_seconds)
                posture_score = min(100.0, (good_seconds / monitoring_seconds) * 100.0)
            result.append(
                {
                    "date": day_key,
                    "monitoring_seconds": monitoring_seconds,
                    "slouch_seconds": slouch_seconds,
                    "event_count": event_count,
                    "posture_score": round(posture_score, 1),
                }
            )
            day += one_day
        return result

    def start_absence(self, timestamp=None):
        """Record the start of an absence period (no pose detected).

        Automatically closes any previously open absence period first, so it is
        safe to call even if a prior absence was never explicitly ended.
        Returns the new row id, or None on failure.
        """
        now = timestamp if timestamp is not None else time.time()
        try:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE absence_periods SET end_time = ? WHERE end_time IS NULL",
                (now,),
            )
            cur.execute(
                "INSERT INTO absence_periods (start_time) VALUES (?)",
                (now,),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as exc:
            print(f"[tracker] failed to start absence: {exc}", file=sys.stderr)
            return None

    def end_absence(self, absence_id, timestamp=None):
        """Record the end of an absence period. Returns True on success."""
        now = timestamp if timestamp is not None else time.time()
        try:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE absence_periods SET end_time = ? WHERE id = ? AND end_time IS NULL",
                (now, absence_id),
            )
            self._conn.commit()
            return True
        except sqlite3.Error as exc:
            print(f"[tracker] failed to end absence {absence_id}: {exc}", file=sys.stderr)
            return False

    def get_today_hourly_stats(self, date=None):
        """Return per-hour posture stats for a single calendar day (24 buckets).

        Each bucket covers one clock hour (0–23).  Hours with no monitoring
        have monitoring_seconds=0 and posture_score=100.0 by convention.
        """
        if date is None:
            date = datetime.date.today()

        day_start_dt = datetime.datetime.combine(date, datetime.time.min)
        day_start_ts = day_start_dt.timestamp()
        day_end_ts = datetime.datetime.combine(date, datetime.time.max).timestamp()

        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT start_time, COALESCE(end_time, ?) AS end_time
                FROM sessions
                WHERE start_time <= ? AND COALESCE(end_time, ?) >= ?
                """,
                (time.time(), day_end_ts, time.time(), day_start_ts),
            )
            sessions = cur.fetchall()
        except sqlite3.Error as exc:
            print(f"[tracker] failed to query sessions for hourly stats: {exc}", file=sys.stderr)
            sessions = []

        try:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT timestamp, duration FROM slouch_events WHERE timestamp >= ? AND timestamp <= ?",
                (day_start_ts, day_end_ts),
            )
            events = cur.fetchall()
        except sqlite3.Error as exc:
            print(f"[tracker] failed to query events for hourly stats: {exc}", file=sys.stderr)
            events = []

        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT start_time, COALESCE(end_time, ?) AS end_time
                FROM absence_periods
                WHERE start_time <= ? AND COALESCE(end_time, ?) >= ?
                """,
                (time.time(), day_end_ts, time.time(), day_start_ts),
            )
            absences = cur.fetchall()
        except sqlite3.Error as exc:
            print(f"[tracker] failed to query absences for hourly stats: {exc}", file=sys.stderr)
            absences = []

        result = []
        for hour in range(24):
            hour_start = (day_start_dt + datetime.timedelta(hours=hour)).timestamp()
            hour_end = (day_start_dt + datetime.timedelta(hours=hour + 1)).timestamp()

            monitoring = 0.0
            for row in sessions:
                s = float(row["start_time"])
                e = float(row["end_time"])
                overlap = min(e, hour_end) - max(s, hour_start)
                if overlap > 0:
                    monitoring += overlap

            absence_seconds = 0.0
            for ab in absences:
                s = float(ab["start_time"])
                e = float(ab["end_time"])
                overlap = min(e, hour_end) - max(s, hour_start)
                if overlap > 0:
                    absence_seconds += overlap
            absence_seconds = min(absence_seconds, monitoring)

            present_seconds = max(0.0, monitoring - absence_seconds)

            slouch_seconds = 0.0
            event_count = 0
            for evt in events:
                ts = float(evt["timestamp"])
                if hour_start <= ts < hour_end:
                    slouch_seconds += float(evt["duration"])
                    event_count += 1

            posture_score = 100.0
            if present_seconds > 0:
                good_time = max(0.0, present_seconds - slouch_seconds)
                posture_score = min(100.0, (good_time / present_seconds) * 100.0)

            result.append(
                {
                    "hour": hour,
                    "monitoring_seconds": monitoring,
                    "absence_seconds": absence_seconds,
                    "present_seconds": present_seconds,
                    "slouch_seconds": slouch_seconds,
                    "event_count": event_count,
                    "posture_score": round(posture_score, 1),
                }
            )

        return result

    def log_good_streak(self, timestamp, duration):
        """Record a completed good-posture streak.

        Returns the new row id, or None on failure.
        """
        try:
            cur = self._conn.cursor()
            # Create table if it doesn't exist (lazy migration)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS good_streaks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    duration REAL NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            cur.execute(
                "INSERT INTO good_streaks (timestamp, duration) VALUES (?, ?)",
                (timestamp, duration),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as exc:
            print(f"[tracker] failed to log good streak: {exc}", file=sys.stderr)
            return None
