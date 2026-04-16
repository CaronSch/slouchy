"""Tests for menubar app logic.

All rumps, posture, and audio internals are mocked.
Tests exercise menu state updates, sensitivity presets, and UI formatting.
"""

import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch, PropertyMock
import time

import pytest

# Mock rumps and heavy dependencies before importing menubar
rumps_mock = MagicMock()
rumps_mock.App = type("App", (), {
    "__init__": lambda self, *a, **kw: None,
    "run": lambda self: None,
    "menu": property(lambda self: {}, lambda self, v: None),
    "title": property(lambda self: "", lambda self, v: None),
})
rumps_mock.MenuItem = MagicMock
rumps_mock.Timer = MagicMock
rumps_mock.notification = MagicMock()
rumps_mock.quit_application = MagicMock()

mp_mock = MagicMock()
cv2_mock = MagicMock()
vision_mock = MagicMock()
base_options_mock = MagicMock()

with patch.dict("sys.modules", {
    "rumps": rumps_mock,
    "mediapipe": mp_mock,
    "mediapipe.tasks": MagicMock(),
    "mediapipe.tasks.python": MagicMock(vision=vision_mock, BaseOptions=base_options_mock),
    "mediapipe.tasks.python.vision": vision_mock,
    "cv2": cv2_mock,
}):
    from menubar import (
        _format_minutes,
        _resolve_menu_icon_path,
        ICON_GOOD,
        ICON_SLOUCHING,
        ICON_SCOLDING,
        ICON_OFFLINE,
        SlouchyApp,
    )
    import config


# ---------------------------------------------------------------------------
# Tests: Duration formatting
# ---------------------------------------------------------------------------

class TestFormatMinutes:
    """Duration format helper for menu titles."""

    def test_seconds(self):
        assert _format_minutes(45) == "45s"

    def test_minutes(self):
        assert _format_minutes(300) == "5m"

    def test_hours(self):
        assert _format_minutes(7500) == "2h 5m"

    def test_zero(self):
        assert _format_minutes(0) == "0s"

    def test_exactly_one_hour(self):
        assert _format_minutes(3600) == "1h 0m"



# ---------------------------------------------------------------------------
# Tests: Icon constants
# ---------------------------------------------------------------------------

class TestIcons:
    """Status icon constants are distinct."""

    def test_all_icons_unique(self):
        icons = {ICON_GOOD, ICON_SLOUCHING, ICON_SCOLDING, ICON_OFFLINE}
        assert len(icons) == 4

    def test_icons_are_emoji(self):
        for icon in [ICON_GOOD, ICON_SLOUCHING, ICON_SCOLDING, ICON_OFFLINE]:
            assert len(icon) > 0


class TestIconPathResolution:
    def test_prefers_menubar_icon_when_present(self):
        os_mod = _resolve_menu_icon_path.__globals__["os"]
        with patch.dict(os_mod.environ, {}, clear=True), \
             patch.object(os_mod.path, "abspath", return_value="/app/menubar.py"), \
             patch.object(os_mod.path, "exists", return_value=True):
            assert _resolve_menu_icon_path() == "/app/menubar_icon.png"


class TestConfidenceGate:
    def _make_app(self, confidence: float):
        app = SimpleNamespace()
        app._monitoring = True
        app._pause_until = None
        app._session_start = None
        app._last_logged_time = None
        app._streak_start = None
        app.title = ""
        app.status_item = SimpleNamespace(title="")
        app.shared_state = SimpleNamespace(
            lock=threading.Lock(),
            is_slouching=False,
            slouch_start_time=None,
            camera_available=True,
            landmark_confidence=confidence,
        )
        app.engine = SimpleNamespace(update=lambda *_: None)
        app.audio = SimpleNamespace(play_tier=lambda *_: None)
        app.tracker = SimpleNamespace(log_slouch=lambda *_: None)
        app._update_menu_ui_called = False
        app._update_menu_ui = lambda *_: setattr(app, "_update_menu_ui_called", True)
        return app

    def test_confidence_above_config_threshold_does_not_show_tracking_lost(self):
        assert config.LANDMARK_CONFIDENCE_MIN < 0.5
        app = self._make_app(confidence=0.4)

        SlouchyApp._poll_callback(app, None)

        assert app.status_item.title != "Tracking lost (stay in frame)"
        assert app._update_menu_ui_called is True


class TestMonitoringStatusReset:
    def test_start_monitoring_clears_stale_calibration_text(self):
        app = SimpleNamespace()
        app._monitoring = False
        app._pause_until = 123.0
        app._session_id = None
        app._session_start = None
        app._last_logged_time = None
        app._streak_start = None
        app.title = ""
        app.status_item = SimpleNamespace(title="Calibrating — sit up straight!")
        app.tracker = SimpleNamespace(start_session=lambda: 42)
        app._detector_thread = None
        app._run_detector = lambda: None
        app._poll_timer = SimpleNamespace(start=lambda: None)
        app._refresh_monitoring_menu = lambda: None

        SlouchyApp._start_monitoring(app)

        assert app.status_item.title == "Monitoring…"


class TestPollingLifecycle:
    def test_start_monitoring_does_not_restart_poll_timer(self):
        app = SimpleNamespace()
        app._monitoring = False
        app._pause_until = None
        app._session_id = None
        app._session_start = None
        app._last_logged_time = None
        app._streak_start = None
        app.title = ""
        app.status_item = SimpleNamespace(title="")
        app.tracker = SimpleNamespace(start_session=lambda: 7)
        app._run_detector = lambda: None
        app._detector_thread = None
        app._poll_timer = SimpleNamespace(start=MagicMock())
        app._refresh_monitoring_menu = lambda: None

        SlouchyApp._start_monitoring(app)

        app._poll_timer.start.assert_not_called()

    def test_stop_monitoring_does_not_stop_poll_timer(self):
        app = SimpleNamespace()
        app._monitoring = True
        app._poll_timer = SimpleNamespace(stop=MagicMock())
        app.detector = SimpleNamespace(stop=lambda: None)
        app._streak_start = None
        app._session_id = None
        app.title = ""
        app.status_item = SimpleNamespace(title="")
        app._refresh_monitoring_menu = lambda: None
        app.tracker = SimpleNamespace(log_good_streak=lambda *_: None, end_session=lambda *_: None)

        SlouchyApp._stop_monitoring(app)

        app._poll_timer.stop.assert_not_called()


class _FakeMenuItem:
    def __init__(self, title=""):
        self.title = title
        self.callback = None

    def set_callback(self, cb):
        self.callback = cb


class TestMonitoringMenuState:
    def test_resume_disabled_when_monitoring_is_on(self):
        app = SimpleNamespace()
        app._monitoring = True
        app._pause_until = None
        app.monitoring_menu = _FakeMenuItem("Monitoring")
        app.resume_item = _FakeMenuItem("Resume")
        app._resume_monitoring = lambda _s=None: None

        SlouchyApp._refresh_monitoring_menu(app)

        assert app.monitoring_menu.title == "Monitoring (On)"
        assert app.resume_item.title == "Resume (Already On)"
        assert app.resume_item.callback is None

    def test_resume_enabled_when_paused(self):
        app = SimpleNamespace()
        app._monitoring = True
        app._pause_until = 999.0
        app.monitoring_menu = _FakeMenuItem("Monitoring")
        app.resume_item = _FakeMenuItem("Resume")
        resume_fn = lambda _s=None: None
        app._resume_monitoring = resume_fn

        SlouchyApp._refresh_monitoring_menu(app)

        assert app.monitoring_menu.title == "Monitoring (Paused)"
        assert app.resume_item.title == "Resume"
        assert app.resume_item.callback is resume_fn
