"""Tests for dashboard HTML rendering behavior."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dashboard import render_dashboard_html


def _today_summary():
    return {
        "total_monitoring_minutes": 15.0,
        "total_slouch_minutes": 1.0,
        "slouch_events": 2,
        "best_streak_minutes": 10.0,
        "posture_score": 93.3,
    }


def test_history_empty_shows_unavailable_message():
    today = datetime.date.today().isoformat()
    recent_days = [
        {
            "date": today,
            "monitoring_seconds": 900.0,
            "slouch_seconds": 60.0,
            "event_count": 2,
            "posture_score": 93.3,
        }
    ]

    html = render_dashboard_html(_today_summary(), recent_days, [])

    assert "History is not available yet." in html
    assert "No data yet." not in html


def test_history_only_includes_days_with_activity():
    today = datetime.date.today()
    d1 = (today - datetime.timedelta(days=1)).isoformat()
    d2 = (today - datetime.timedelta(days=2)).isoformat()
    recent_days = [
        {
            "date": today.isoformat(),
            "monitoring_seconds": 900.0,
            "slouch_seconds": 60.0,
            "event_count": 2,
            "posture_score": 93.3,
        },
        {
            "date": d1,
            "monitoring_seconds": 0.0,
            "slouch_seconds": 0.0,
            "event_count": 0,
            "posture_score": 100.0,
        },
        {
            "date": d2,
            "monitoring_seconds": 300.0,
            "slouch_seconds": 30.0,
            "event_count": 1,
            "posture_score": 90.0,
        },
    ]

    html = render_dashboard_html(_today_summary(), recent_days, [])

    assert d2 in html
    assert d1 not in html


def test_rewards_section_present_with_badges_and_points():
    today = datetime.date.today()
    recent_days = [
        {
            "date": today.isoformat(),
            "monitoring_seconds": 7200.0,  # 120m
            "slouch_seconds": 0.0,
            "event_count": 0,
            "posture_score": 100.0,
        }
    ]
    summary = {
        "total_monitoring_minutes": 120.0,
        "total_slouch_minutes": 0.0,
        "slouch_events": 0,
        "best_streak_minutes": 65.0,
        "posture_score": 100.0,
    }

    html = render_dashboard_html(summary, recent_days, [])

    assert "Rewards" in html
    assert "points" in html
    assert "No-Slouch Hero" in html


def test_recent_events_use_user_friendly_reminder_labels():
    recent_events = [
        {
            "timestamp": datetime.datetime.now().timestamp(),
            "duration": 30.0,
            "tier": 2,
        }
    ]

    html = render_dashboard_html(_today_summary(), [], recent_events)

    assert "Strong reminder" in html
    assert ">Tier<" not in html
