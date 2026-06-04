"""Tests for notification.py — message formatting and priority mapping."""
import pytest
from galaxy.parser import GalaxyEvent
from notification import (
    MessageEvent,
    format_notification_text,
    get_event_priority,
    _map_priority_to_notify_type,
)
import apprise


# ── get_event_priority ────────────────────────────────────────────────────────

class TestGetEventPriority:
    PRIO_MAP = {"BA": 5, "CL": 2, "OP": 2}

    def test_known_code_returns_mapped_priority(self):
        assert get_event_priority("BA", self.PRIO_MAP, 3) == 5

    def test_unknown_code_returns_default(self):
        assert get_event_priority("ZZ", self.PRIO_MAP, 3) == 3

    def test_none_code_returns_default(self):
        assert get_event_priority(None, self.PRIO_MAP, 3) == 3


# ── _map_priority_to_notify_type ──────────────────────────────────────────────

class TestMapPriorityToNotifyType:
    def test_priority_1_is_info(self):
        assert _map_priority_to_notify_type(1) == apprise.NotifyType.INFO

    def test_priority_2_is_info(self):
        assert _map_priority_to_notify_type(2) == apprise.NotifyType.INFO

    def test_priority_3_is_success(self):
        assert _map_priority_to_notify_type(3) == apprise.NotifyType.SUCCESS

    def test_priority_4_is_warning(self):
        assert _map_priority_to_notify_type(4) == apprise.NotifyType.WARNING

    def test_priority_5_is_failure(self):
        assert _map_priority_to_notify_type(5) == apprise.NotifyType.FAILURE


# ── format_notification_text — GalaxyEvent ────────────────────────────────────

class TestFormatNotificationTextGalaxyEvent:
    def _make_event(self, **kwargs) -> GalaxyEvent:
        e = GalaxyEvent()
        for k, v in kwargs.items():
            setattr(e, k, v)
        return e

    def test_action_text_used_when_present(self):
        e = self._make_event(time="11:45", action_text="+CLOSE Living room")
        result = format_notification_text(e)
        assert "11:45" in result
        assert "Living room" in result

    def test_zone_appended_if_not_in_action_text(self):
        e = self._make_event(time="11:45", action_text="+CLOSE Front door", zone="42")
        result = format_notification_text(e)
        assert "42" in result

    def test_zone_not_duplicated_if_already_in_action_text(self):
        e = self._make_event(time="11:45", action_text="+CLOSE Zone 42", zone="42")
        result = format_notification_text(e)
        assert result.count("42") == 1

    def test_sia_level2_fallback_includes_event_code(self):
        e = self._make_event(
            time="11:46",
            event_code="BA",
            event_description="Burglary Alarm",
        )
        result = format_notification_text(e)
        assert "BA" in result
        assert "Burglary" in result

    def test_sia_level2_fallback_includes_subscriber(self):
        e = self._make_event(time="11:45", event_code="CL",
                             event_description="Closing", subscriber_id="23")
        result = format_notification_text(e)
        assert "23" in result

    def test_sia_level2_fallback_includes_zone(self):
        e = self._make_event(time="11:46", event_code="BA",
                             event_description="Burglary Alarm", zone="11")
        result = format_notification_text(e)
        assert "11" in result

    def test_sia_level2_fallback_includes_area(self):
        e = self._make_event(time="11:45", event_code="CL",
                             event_description="Closing", area_id="1")
        result = format_notification_text(e)
        assert "Area" in result
        assert "1" in result

    def test_sia_level2_fallback_includes_peripheral(self):
        e = self._make_event(time="11:45", event_code="CL",
                             event_description="Closing", peripheral_id="10")
        result = format_notification_text(e)
        assert "Peripheral" in result

    def test_sia_level2_fallback_includes_value(self):
        e = self._make_event(time="11:45", event_code="RP",
                             event_description="Automatic Test", value="1440")
        result = format_notification_text(e)
        assert "1440" in result

    def test_missing_time_shows_placeholder(self):
        e = self._make_event(event_code="CL", event_description="Closing")
        result = format_notification_text(e)
        assert "??" in result

    def test_result_is_stripped(self):
        e = self._make_event(time="11:45", action_text="  text  ")
        result = format_notification_text(e)
        assert result == result.strip()


# ── format_notification_text — MessageEvent ───────────────────────────────────

class TestFormatNotificationTextMessageEvent:
    def test_returns_action_text_directly(self):
        msg = MessageEvent("023499", "Home", "Heartbeat lost", priority=4)
        assert format_notification_text(msg) == "Heartbeat lost"

    def test_does_not_prepend_time(self):
        msg = MessageEvent("023499", "Home", "Connection restored", priority=2)
        result = format_notification_text(msg)
        assert "??" not in result
        assert result == "Connection restored"


# ── MessageEvent attributes ───────────────────────────────────────────────────

class TestMessageEvent:
    def test_attributes_set_correctly(self):
        msg = MessageEvent("023499", "Home", "test message", priority=4)
        assert msg.account == "023499"
        assert msg.site_name == "Home"
        assert msg.action_text == "test message"
        assert msg.priority == 4

    def test_event_code_is_none(self):
        msg = MessageEvent("023499", "Home", "test", priority=3)
        assert msg.event_code is None
