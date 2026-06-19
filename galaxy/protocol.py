"""
Galaxy SIA Protocol framing helpers.

Single source of truth for the low-level block format:

    [length_byte][command_byte][payload...][checksum]

where length_byte = 0x40 + len(payload) and checksum is the XOR of 0xFF
with every preceding byte of the block.

Used by sia-server.py, ip_check.py, the test client and the test suite so
the checksum / framing logic is implemented exactly once.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

# Framing constants
HEADER_SIZE = 2          # length byte + command byte
CHECKSUM_SIZE = 1
MIN_BLOCK_SIZE = HEADER_SIZE + CHECKSUM_SIZE  # 3
LENGTH_OFFSET = 0x40
MAX_PAYLOAD = 0xFF - LENGTH_OFFSET            # 191 bytes


def xor_checksum(data: bytes) -> int:
    """XOR checksum used by the Galaxy SIA protocol (seed 0xFF)."""
    checksum = 0xFF
    for byte in data:
        checksum ^= byte
    return checksum


def build_block(command_byte: int, payload: bytes = b'') -> bytes:
    """Build a complete, checksummed SIA block."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(
            f"Payload too long ({len(payload)} bytes); the single-byte "
            f"length field supports at most {MAX_PAYLOAD} bytes."
        )
    message = bytes([LENGTH_OFFSET + len(payload), command_byte]) + payload
    return message + bytes([xor_checksum(message)])


def expected_block_size(first_byte: int) -> Optional[int]:
    """
    Total on-the-wire size of a block whose first (length) byte is given.
    Returns None if the length byte is not plausible.
    """
    payload_len = first_byte - LENGTH_OFFSET
    if payload_len < 0 or payload_len > MAX_PAYLOAD:
        return None
    return payload_len + MIN_BLOCK_SIZE


def validate_and_strip(block: bytes) -> Tuple[Optional[int], Optional[bytes]]:
    """
    Validates a complete raw block and returns (command_byte, payload),
    or (None, None) if the block is malformed.
    """
    if len(block) < MIN_BLOCK_SIZE:
        return None, None
    size = expected_block_size(block[0])
    if size is None or size != len(block):
        return None, None
    if xor_checksum(block[:-1]) != block[-1]:
        return None, None
    return block[1], block[2:-1]


def extract_blocks(buffer: bytearray) -> Tuple[List[bytes], bool]:
    """
    Extracts all complete blocks from the front of `buffer` (mutated in place).

    TCP gives no message boundaries: a single read() can contain several
    blocks, or only part of one. This function consumes as many complete
    blocks as the buffer holds and leaves any trailing partial block in
    place for the next read.

    Returns (blocks, ok). ok=False means the buffer starts with an
    implausible length byte (stream is desynchronised / garbage) and the
    caller should reject the connection.
    """
    blocks: List[bytes] = []
    while buffer:
        size = expected_block_size(buffer[0])
        if size is None:
            return blocks, False
        if len(buffer) < size:
            break  # partial block - wait for more data
        blocks.append(bytes(buffer[:size]))
        del buffer[:size]
    return blocks, True
