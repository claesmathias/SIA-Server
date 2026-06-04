"""Tests for the SIA framing helpers in sia-server.py.

validate_and_strip and build_and_send aren't importable as standalone functions,
so we re-implement the frame logic here and test it end-to-end against the
constants from galaxy.constants.
"""
import pytest
from galaxy.constants import COMMANDS, COMMAND_BYTES


# ── Frame building / parsing helpers (mirrors sia-server.py logic) ────────────

def build_frame(command: str, payload: bytes = b"") -> bytes:
    command_byte = COMMAND_BYTES[command]
    length_byte = len(payload) + 0x40
    msg = bytes([length_byte, command_byte]) + payload
    checksum = 0xFF
    for b in msg:
        checksum ^= b
    return msg + bytes([checksum])


def parse_frame(data: bytes):
    """Returns (command_name, payload) or (None, None) on error."""
    if len(data) < 3:
        return None, None
    declared_len = data[0] - 0x40
    actual_len = len(data) - 3
    if declared_len != actual_len:
        return None, None
    checksum = 0xFF
    for b in data[:-1]:
        checksum ^= b
    if checksum != data[-1]:
        return None, None
    cmd_byte = data[1]
    cmd_name = COMMANDS.get(cmd_byte, f"UNKNOWN(0x{cmd_byte:02x})")
    payload = data[2:-1]
    return cmd_name, payload


# ── Frame construction ────────────────────────────────────────────────────────

class TestBuildFrame:
    def test_empty_payload_length_byte(self):
        frame = build_frame("ACKNOWLEDGE")
        assert frame[0] == 0x40  # length_byte = 0 + 0x40

    def test_payload_sets_length_correctly(self):
        frame = build_frame("ACCOUNT_ID", b"023499")
        assert frame[0] == 0x40 + 6

    def test_command_byte_correct(self):
        frame = build_frame("ACKNOWLEDGE")
        assert frame[1] == 0x38

    def test_reject_command_byte(self):
        frame = build_frame("REJECT")
        assert frame[1] == 0x39

    def test_checksum_valid(self):
        frame = build_frame("ACCOUNT_ID", b"023499")
        cmd, payload = parse_frame(frame)
        assert cmd == "ACCOUNT_ID"
        assert payload == b"023499"


# ── Frame parsing ─────────────────────────────────────────────────────────────

class TestParseFrame:
    def test_valid_ack_roundtrips(self):
        frame = build_frame("ACKNOWLEDGE")
        cmd, payload = parse_frame(frame)
        assert cmd == "ACKNOWLEDGE"
        assert payload == b""

    def test_valid_account_id_roundtrips(self):
        frame = build_frame("ACCOUNT_ID", b"023499")
        cmd, payload = parse_frame(frame)
        assert cmd == "ACCOUNT_ID"
        assert payload == b"023499"

    def test_valid_new_event_roundtrips(self):
        payload_data = b"ti11:45/id001/pi010/CL"
        frame = build_frame("NEW_EVENT", payload_data)
        cmd, payload = parse_frame(frame)
        assert cmd == "NEW_EVENT"
        assert payload == payload_data

    def test_valid_old_event_roundtrips(self):
        frame = build_frame("OLD_EVENT", b"ti11:45/CL")
        cmd, payload = parse_frame(frame)
        assert cmd == "OLD_EVENT"
        assert payload == b"ti11:45/CL"

    def test_too_short_returns_none(self):
        cmd, payload = parse_frame(b"\x40\x38")
        assert cmd is None

    def test_length_mismatch_returns_none(self):
        # Build a valid frame then corrupt the length byte
        frame = bytearray(build_frame("ACKNOWLEDGE"))
        frame[0] = 0x45  # claim 5 payload bytes, but there are 0
        cmd, payload = parse_frame(bytes(frame))
        assert cmd is None

    def test_bad_checksum_returns_none(self):
        frame = bytearray(build_frame("ACKNOWLEDGE"))
        frame[-1] ^= 0xFF  # flip checksum bits
        cmd, payload = parse_frame(bytes(frame))
        assert cmd is None

    def test_garbage_returns_none(self):
        cmd, payload = parse_frame(b"\xff\xff\xff\xff")
        assert cmd is None

    def test_unknown_command_byte_still_parses(self):
        # SIA-Server-1 passes unknown commands through (unlike the HACS version)
        # Build a raw frame with an unknown command byte 0x99
        payload_data = b"data"
        length_byte = len(payload_data) + 0x40
        msg = bytes([length_byte, 0x99]) + payload_data
        checksum = 0xFF
        for b in msg:
            checksum ^= b
        frame = msg + bytes([checksum])
        cmd, payload = parse_frame(frame)
        assert cmd == "UNKNOWN(0x99)"
        assert payload == payload_data

    def test_end_of_data_roundtrips(self):
        frame = build_frame("END_OF_DATA")
        cmd, payload = parse_frame(frame)
        assert cmd == "END_OF_DATA"
        assert payload == b""


# ── COMMANDS / COMMAND_BYTES consistency ─────────────────────────────────────

class TestCommandsDict:
    def test_commands_and_command_bytes_are_inverse(self):
        for byte, name in COMMANDS.items():
            assert COMMAND_BYTES[name] == byte

    def test_new_event_is_alarm(self):
        assert COMMANDS[0x4E] == "NEW_EVENT"

    def test_old_event_is_non_alarm(self):
        assert COMMANDS[0x4F] == "OLD_EVENT"

    def test_acknowledge_byte(self):
        assert COMMAND_BYTES["ACKNOWLEDGE"] == 0x38

    def test_reject_byte(self):
        assert COMMAND_BYTES["REJECT"] == 0x39
