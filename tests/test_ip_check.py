"""Tests for ip_check.py helpers (imported lazily to avoid config side effects).

ip_check.py parses argv and loads sia-server.conf at module import time, so
it can't be imported directly in a test process. We exec just the pure
helper functions (validate_ip_check_packet, extract_account,
lookup_account_key) in an isolated namespace instead.
"""
import os
import types


def _load_helpers():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ip_check.py')
    src = open(path).read()
    ns = {}
    start = src.index('def validate_ip_check_packet')
    end = src.index('async def handle_ip_check')
    exec(compile(src[start:end], 'ip_check_helpers', 'exec'),
         {'log': types.SimpleNamespace(debug=lambda *a: None)}, ns)
    return ns


class TestValidateIpCheckPacket:
    def test_valid_packet_accepted(self):
        h = _load_helpers()
        good = bytes([0x00]) + b'00023499' + b'\x00' * 17
        assert len(good) == 26
        assert h['validate_ip_check_packet'](good)

    def test_wrong_length_rejected(self):
        h = _load_helpers()
        good = bytes([0x00]) + b'00023499' + b'\x00' * 17
        assert not h['validate_ip_check_packet'](good[:-1])

    def test_bad_header_byte_rejected(self):
        h = _load_helpers()
        good = bytes([0x00]) + b'00023499' + b'\x00' * 17
        assert not h['validate_ip_check_packet'](b'\x01' + good[1:])


class TestExtractAccount:
    def test_keeps_zero_padding(self):
        h = _load_helpers()
        pkt = bytes([0x00]) + b'00023499' + b'\x00' * 17
        assert h['extract_account'](pkt) == '00023499'


class TestLookupAccountKey:
    def test_matches_config_form_with_leading_zeros_stripped(self):
        """Regression: '00023499' from the packet must match config key '023499'."""
        h = _load_helpers()
        keys = {'023499', '1234', 'default'}
        assert h['lookup_account_key']('00023499', keys) == '023499'
        assert h['lookup_account_key']('00001234', keys) == '1234'

    def test_exact_match_returned_as_is(self):
        h = _load_helpers()
        keys = {'023499', 'default'}
        assert h['lookup_account_key']('023499', keys) == '023499'

    def test_unknown_account_falls_back_to_stripped(self):
        h = _load_helpers()
        keys = {'023499', 'default'}
        assert h['lookup_account_key']('00009999', keys) == '9999'

    def test_config_key_itself_starting_with_zero_is_matched(self):
        """A config key like '01234' (genuinely starts with 0) must still match."""
        h = _load_helpers()
        keys = {'01234', 'default'}
        assert h['lookup_account_key']('00001234', keys) == '01234'
