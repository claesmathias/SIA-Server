"""Tests for galaxy.protocol: checksum, block build/validate, stream framing.

These import the real production module (used by sia-server.py, ip_check.py
and the test client) rather than reimplementing the logic, so a regression
in the actual framing code is caught here.
"""
import pytest
from galaxy.protocol import (
    build_block, validate_and_strip, extract_blocks,
    expected_block_size, xor_checksum, MAX_PAYLOAD,
)
from galaxy.constants import COMMANDS, COMMAND_BYTES


# ── Block build / validate ────────────────────────────────────────────────────

class TestBuildBlock:
    def test_empty_payload_length_byte(self):
        block = build_block(COMMAND_BYTES['ACKNOWLEDGE'])
        assert block[0] == 0x40

    def test_payload_sets_length_correctly(self):
        block = build_block(COMMAND_BYTES['ACCOUNT_ID'], b'023499')
        assert block[0] == 0x40 + 6

    def test_command_byte_correct(self):
        block = build_block(COMMAND_BYTES['ACKNOWLEDGE'])
        assert block[1] == 0x38

    def test_reject_command_byte(self):
        block = build_block(COMMAND_BYTES['REJECT'])
        assert block[1] == 0x39

    def test_roundtrips_through_validate(self):
        block = build_block(COMMAND_BYTES['ACCOUNT_ID'], b'023499')
        cmd, payload = validate_and_strip(block)
        assert cmd == COMMAND_BYTES['ACCOUNT_ID']
        assert payload == b'023499'

    def test_payload_too_long_raises(self):
        with pytest.raises(ValueError):
            build_block(0x4E, b'x' * (MAX_PAYLOAD + 1))


class TestValidateAndStrip:
    def test_valid_ack_roundtrips(self):
        block = build_block(COMMAND_BYTES['ACKNOWLEDGE'])
        cmd, payload = validate_and_strip(block)
        assert cmd == COMMAND_BYTES['ACKNOWLEDGE']
        assert payload == b''

    def test_valid_new_event_roundtrips(self):
        payload_data = b'ti11:45/id001/pi010/CL'
        block = build_block(COMMAND_BYTES['NEW_EVENT'], payload_data)
        cmd, payload = validate_and_strip(block)
        assert cmd == COMMAND_BYTES['NEW_EVENT']
        assert payload == payload_data

    def test_valid_old_event_roundtrips(self):
        block = build_block(COMMAND_BYTES['OLD_EVENT'], b'ti11:45/CL')
        cmd, payload = validate_and_strip(block)
        assert cmd == COMMAND_BYTES['OLD_EVENT']
        assert payload == b'ti11:45/CL'

    def test_known_wire_sample(self):
        # ACCOUNT_ID '023499' from the README hex segment examples
        block = bytes.fromhex('46233032333439399f')
        cmd, payload = validate_and_strip(block)
        assert cmd == 0x23
        assert payload == b'023499'

    def test_too_short_returns_none(self):
        assert validate_and_strip(b'\x40\x38') == (None, None)
        assert validate_and_strip(b'') == (None, None)

    def test_length_mismatch_returns_none(self):
        block = bytearray(build_block(COMMAND_BYTES['ACKNOWLEDGE']))
        block[0] = 0x45  # claim 5 payload bytes, but there are 0
        assert validate_and_strip(bytes(block)) == (None, None)

    def test_length_mismatch_trailing_extra_bytes(self):
        block = build_block(0x4E, b'abc') + b'extra'
        assert validate_and_strip(block) == (None, None)

    def test_bad_checksum_returns_none(self):
        block = bytearray(build_block(COMMAND_BYTES['ACKNOWLEDGE']))
        block[-1] ^= 0xFF
        assert validate_and_strip(bytes(block)) == (None, None)

    def test_garbage_returns_none(self):
        assert validate_and_strip(b'\xff\xff\xff\xff') == (None, None)

    def test_unknown_command_byte_still_parses(self):
        # Unknown command bytes pass through (the caller maps to UNKNOWN(0xXX))
        payload_data = b'data'
        block = build_block(0x99, payload_data)
        cmd, payload = validate_and_strip(block)
        assert cmd == 0x99
        assert payload == payload_data

    def test_end_of_data_roundtrips(self):
        block = build_block(COMMAND_BYTES['END_OF_DATA'])
        cmd, payload = validate_and_strip(block)
        assert cmd == COMMAND_BYTES['END_OF_DATA']
        assert payload == b''


class TestExpectedBlockSize:
    def test_minimum_length_byte(self):
        assert expected_block_size(0x40) == 3

    def test_typical_length_byte(self):
        assert expected_block_size(0x46) == 9

    def test_below_offset_is_implausible(self):
        assert expected_block_size(0x3F) is None

    def test_max_payload_accepted(self):
        assert expected_block_size(0xFF) == MAX_PAYLOAD + 3


class TestXorChecksum:
    def test_seed_value(self):
        assert xor_checksum(b'') == 0xFF


# ── Stream framing: TCP reassembly (the bug class the old code missed) ───────

def _sample_blocks():
    return [
        build_block(COMMAND_BYTES['ACCOUNT_ID'], b'023499'),
        build_block(COMMAND_BYTES['NEW_EVENT'], b'ti23:42/id023/pi013/CG'),
        build_block(COMMAND_BYTES['ASCII'], b' PART SET USER'),
        build_block(COMMAND_BYTES['END_OF_DATA']),
    ]


class TestExtractBlocks:
    def test_coalesced_in_one_read(self):
        """Several blocks arriving in a single TCP read must all be extracted."""
        buf = bytearray(b''.join(_sample_blocks()))
        blocks, ok = extract_blocks(buf)
        assert ok
        assert len(blocks) == 4
        assert buf == b''

    def test_split_across_reads(self):
        """A block split across two reads must wait for the second part."""
        whole = _sample_blocks()[1]
        buf = bytearray(whole[:5])
        blocks, ok = extract_blocks(buf)
        assert ok and blocks == []  # partial: nothing yet
        buf.extend(whole[5:])
        blocks, ok = extract_blocks(buf)
        assert ok and len(blocks) == 1
        assert blocks[0] == whole

    def test_empty_buffer_returns_nothing(self):
        buf = bytearray()
        blocks, ok = extract_blocks(buf)
        assert ok and blocks == []

    def test_garbage_detected(self):
        buf = bytearray(b'\x00\x01\x02')  # length byte < 0x40
        blocks, ok = extract_blocks(buf)
        assert not ok and blocks == []

    def test_garbage_after_valid_block(self):
        buf = bytearray(_sample_blocks()[0] + b'\x05garbage')
        blocks, ok = extract_blocks(buf)
        assert len(blocks) == 1
        assert not ok

    def test_multiple_full_blocks_plus_trailing_partial(self):
        whole = _sample_blocks()
        buf = bytearray(whole[0] + whole[1] + whole[2][:3])
        blocks, ok = extract_blocks(buf)
        assert ok
        assert len(blocks) == 2
        assert bytes(buf) == whole[2][:3]  # partial block left for next read


# ── COMMANDS / COMMAND_BYTES consistency ─────────────────────────────────────

class TestCommandsDict:
    def test_commands_and_command_bytes_are_inverse(self):
        for byte, name in COMMANDS.items():
            assert COMMAND_BYTES[name] == byte

    def test_new_event_is_alarm(self):
        assert COMMANDS[0x4E] == 'NEW_EVENT'

    def test_old_event_is_non_alarm(self):
        assert COMMANDS[0x4F] == 'OLD_EVENT'

    def test_acknowledge_byte(self):
        assert COMMAND_BYTES['ACKNOWLEDGE'] == 0x38

    def test_reject_byte(self):
        assert COMMAND_BYTES['REJECT'] == 0x39
