"""Tests for galaxy/parser.py — payload parsing and event building."""
import pytest
from galaxy.parser import (
    GalaxyEvent,
    decode_unknown_text,
    parse_data_payload,
    parse_account_payload,
    parse_ascii_payload,
    parse_galaxy_event,
)
from galaxy.constants import EVENT_CODE_DESCRIPTIONS, UNKNOWN_CHAR_MAP


# ── decode_unknown_text ────────────────────────────────────────────────────────

class TestDecodeUnknownText:
    def test_plain_ascii(self):
        assert decode_unknown_text(b"CLOSE", UNKNOWN_CHAR_MAP) == "CLOSE"

    def test_strips_whitespace(self):
        assert decode_unknown_text(b"  CLOSE  ", UNKNOWN_CHAR_MAP) == "CLOSE"

    def test_override_0xE9_to_oslash_upper(self):
        result = decode_unknown_text(bytes([0xE9]), UNKNOWN_CHAR_MAP)
        assert result == "Ø"

    def test_override_0xED_to_oslash_lower(self):
        result = decode_unknown_text(bytes([0xED]), UNKNOWN_CHAR_MAP)
        assert result == "ø"

    def test_mixed_ascii_and_override(self):
        result = decode_unknown_text(b"D" + bytes([0xE9]) + b"R", UNKNOWN_CHAR_MAP)
        assert result == "DØR"

    def test_empty_bytes_returns_empty(self):
        assert decode_unknown_text(b"", UNKNOWN_CHAR_MAP) == ""


# ── parse_data_payload ────────────────────────────────────────────────────────

class TestParseDataPayload:
    def _parse(self, raw: str) -> GalaxyEvent:
        event = GalaxyEvent()
        parse_data_payload(raw.encode(), event, EVENT_CODE_DESCRIPTIONS)
        return event

    def test_simple_close_event(self):
        e = self._parse("ti11:45/id001/pi010/CL")
        assert e.event_code == "CL"
        assert e.time == "11:45"

    def test_alarm_with_zone(self):
        e = self._parse("ti11:46/BA1011")
        assert e.event_code == "BA"
        assert e.zone == "1011"

    def test_alarm_with_3digit_zone(self):
        e = self._parse("BA123")
        assert e.event_code == "BA"
        assert e.zone == "123"

    def test_event_code_only(self):
        e = self._parse("CL")
        assert e.event_code == "CL"
        assert e.zone is None

    def test_subscriber_id_leading_zeros_stripped(self):
        e = self._parse("ti11:45/id0023/CL")
        assert e.subscriber_id == "23"

    def test_subscriber_id_all_zeros(self):
        e = self._parse("ti11:45/id000/CL")
        assert e.subscriber_id == "0"

    def test_area_id_leading_zeros_stripped(self):
        e = self._parse("ti11:45/ri0001/CL")
        assert e.area_id == "1"

    def test_peripheral_id_leading_zeros_stripped(self):
        e = self._parse("ti11:45/pi0010/CL")
        assert e.peripheral_id == "10"

    def test_value_leading_zeros_stripped(self):
        e = self._parse("ti11:45/va0060/RP")
        assert e.value == "60"

    def test_zone_leading_zeros_stripped(self):
        e = self._parse("BA0011")
        assert e.zone == "11"

    def test_date_field(self):
        e = self._parse("da0604/ti1145/CL")
        assert e.date == "0604"
        assert e.time == "1145"

    def test_automated_id(self):
        e = self._parse("ti11:45/ai0001/CL")
        assert e.automated_id == "1"

    def test_phone_id(self):
        e = self._parse("ti11:45/ph0001/CL")
        assert e.phone_id == "0001"

    def test_sia_level(self):
        e = self._parse("ti11:45/lv0003/CL")
        assert e.sia_level == "0003"

    def test_sia_subscriber_id(self):
        e = self._parse("ti11:45/ss0001/CL")
        assert e.sia_subscriber_id == "0001"

    def test_route_group(self):
        e = self._parse("ti11:45/rg01/CL")
        assert e.route_group == "01"

    def test_sia_path(self):
        e = self._parse("ti11:45/pt001/CL")
        assert e.sia_path == "001"

    def test_event_description_lookup(self):
        e = self._parse("CL")
        assert e.event_description is not None
        assert e.event_description != "Unknown"

    def test_unknown_event_code_description(self):
        e = self._parse("ZZ")
        assert e.event_description == "Unknown"

    def test_all_fields_full_payload(self):
        e = self._parse("da0604/ti1145/id0023/ri0001/pi0010/CL")
        assert e.date == "0604"
        assert e.time == "1145"
        assert e.subscriber_id == "23"
        assert e.area_id == "1"
        assert e.peripheral_id == "10"
        assert e.event_code == "CL"

    def test_empty_payload_no_crash(self):
        e = self._parse("")
        assert e.event_code is None

    def test_event_code_can_appear_alone_without_trailing_slash(self):
        e = self._parse("BA")
        assert e.event_code == "BA"


# ── parse_account_payload ─────────────────────────────────────────────────────

class TestParseAccountPayload:
    def test_parses_account_number(self):
        event = GalaxyEvent()
        parse_account_payload(b"023499", event)
        assert event.account == "023499"

    def test_stores_raw_payload(self):
        event = GalaxyEvent()
        parse_account_payload(b"023499", event)
        assert event.account_payload == b"023499"


# ── parse_ascii_payload ───────────────────────────────────────────────────────

class TestParseAsciiPayload:
    def test_basic_ascii(self):
        event = GalaxyEvent()
        parse_ascii_payload(b"+CLOSE Living room", event, UNKNOWN_CHAR_MAP)
        assert event.action_text == "+CLOSE Living room"

    def test_stores_raw_payload(self):
        event = GalaxyEvent()
        parse_ascii_payload(b"text", event, UNKNOWN_CHAR_MAP)
        assert event.ascii_payload == b"text"


# ── parse_galaxy_event ────────────────────────────────────────────────────────

class TestParseGalaxyEvent:
    SITES = {"023499": "Home"}
    CHAR_MAP = UNKNOWN_CHAR_MAP

    def _run(self, blocks):
        return parse_galaxy_event(blocks, self.SITES, self.CHAR_MAP, EVENT_CODE_DESCRIPTIONS)

    def test_new_event_sets_is_alarm_true(self):
        blocks = [
            {"command": "ACCOUNT_ID", "payload": b"023499"},
            {"command": "NEW_EVENT",  "payload": b"ti11:45/CL"},
        ]
        e = self._run(blocks)
        assert e.is_alarm is True

    def test_old_event_sets_is_alarm_false(self):
        blocks = [
            {"command": "ACCOUNT_ID", "payload": b"023499"},
            {"command": "OLD_EVENT",  "payload": b"ti11:45/CL"},
        ]
        e = self._run(blocks)
        assert e.is_alarm is False

    def test_site_name_resolved_from_account(self):
        blocks = [{"command": "ACCOUNT_ID", "payload": b"023499"}]
        e = self._run(blocks)
        assert e.site_name == "Home"

    def test_unknown_account_falls_back_to_number(self):
        blocks = [{"command": "ACCOUNT_ID", "payload": b"999999"}]
        e = self._run(blocks)
        assert e.site_name == "999999"

    def test_ascii_block_captured(self):
        blocks = [
            {"command": "ACCOUNT_ID", "payload": b"023499"},
            {"command": "NEW_EVENT",  "payload": b"ti11:45/CL"},
            {"command": "ASCII",      "payload": b"+CLOSE Living room"},
        ]
        e = self._run(blocks)
        assert "Living room" in e.action_text

    def test_full_sequence(self):
        blocks = [
            {"command": "ACCOUNT_ID", "payload": b"023499"},
            {"command": "NEW_EVENT",  "payload": b"ti11:46/BA1011"},
        ]
        e = self._run(blocks)
        assert e.account == "023499"
        assert e.event_code == "BA"
        assert e.zone == "1011"
        assert e.is_alarm is True


# ── constants sanity checks ───────────────────────────────────────────────────

class TestConstants:
    def test_cb_night_set_present(self):
        assert "CB" in EVENT_CODE_DESCRIPTIONS
        assert EVENT_CODE_DESCRIPTIONS["CB"] == "Night Set"

    def test_new_event_and_old_event_in_commands(self):
        from galaxy.constants import COMMANDS
        assert 0x4E in COMMANDS  # NEW_EVENT / Alarm
        assert 0x4F in COMMANDS  # OLD_EVENT / Non-Alarm

    def test_acknowledge_and_reject_in_commands(self):
        from galaxy.constants import COMMANDS
        assert 0x38 in COMMANDS  # ACKNOWLEDGE
        assert 0x39 in COMMANDS  # REJECT
