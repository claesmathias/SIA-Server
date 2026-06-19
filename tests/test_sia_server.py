#!/usr/bin/env python3
"""SIA Server Test Client

Builds and sends valid Galaxy SIA message blocks to the SIA server and
reports the server's ACK/REJECT response to each block.

--- Arguments ---

  --host HOST         Server hostname or IP address (default: 127.0.0.1)
  --port PORT         Server port (default: 10000)
  --timeout SECS      Max seconds to wait for a response per segment (default: 2.0)
  --delay SECS        Delay between segments in seconds (default: 0.05)
  --quiet             Suppress per-segment output; only print final result

Message modes (mutually exclusive, one is required):
  --send-sample                     Send the built-in sample message sequence.
  --segment HEX [--segment HEX]     Send one or more raw hex segments.
  --account-id ID --new-event EVT   Build and send an alarm event message.
  --account-id ID --old-event EVT   Build and send a non-alarm event message.
  --ascii TEXT                      Optional ASCII block (omit for SIA level 0/1/2).

--- SIA Event Structure Reference ---

Event types and their DATA block (N/O) formats by SIA level:

  Zone events (detector alarm, keyswitch etc.):
    Level 3/4:  #xxxxxx  Ntihh:mm/rigg/EVzzzz   + ASCII block
    Level 2:    #xxxxxx  Ntihh:mm/rigg/EVzzzz
    Level 1:    #xxxxxx  NEVzzzz
    Level 0:    #xxxx    NEVzzzz

  User events (arm/disarm, reset, duress etc.):
    Level 3/4:  #xxxxxx  Ntihh:mm/rigg/iduuu/pimmm/EV   + ASCII block
    Level 2:    #xxxxxx  Ntihh:mm/rigg/iduuu/pimmm/EV
    Level 1:    #xxxxxx  NEVmmm
    Level 0:    #xxxx    NEVmmm

  Module events (keypad added, RIO missing etc.):
    Level 3/4:  #xxxxxx  Ntihh:mm/rigg/pimmm/EV   + ASCII block
    Level 2:    #xxxxxx  Ntihh:mm/rigg/pimmm/EV
    Level 1:    #xxxxxx  NEVmmm
    Level 0:    #xxxx    NEVmmm

  System events (auto set, test, engineer mode etc.):
    Level 3/4:  #xxxxxx  Ntihh:mm/rigg/EV   + ASCII block
    Level 2:    #xxxxxx  Ntihh:mm/rigg/EV
    Level 1:    #xxxxxx  NEV
    Level 0:    #xxxx    NEV000

  'N' = NEW_EVENT (0x4E, alarm), 'O' = OLD_EVENT (0x4F, non-alarm)
  SIA Level 0 uses a 4-digit account number (#xxxx).
  SIA Level 1 and above use a 6-digit account number (#xxxxxx).
  Field codes: ti=time  ri=area  id=subscriber  pi=peripheral  EV=event_code
               zone digits follow the event code directly (no separator).

--- Examples ---

  # Send built-in sample (part-set, SIA Level 3):
  python tests/test_sia_server.py --send-sample

  # Alarm event, SIA Level 2 (no ASCII block):
  python tests/test_sia_server.py --account-id 023499 \\
    --new-event 'ti23:42/ri01/id023/BA1011'

  # Non-alarm event with ASCII block (SIA Level 3):
  python tests/test_sia_server.py --account-id 023499 \\
    --old-event 'ti23:42/id023/pi013/CG' --ascii ' PART SET USER'

  # Raw hex segments:
  python tests/test_sia_server.py \\
    --segment 46233032333439399f \\
    --segment 564e746932333a34322f69643032332f70693031332f4347fb \\
    --segment 4e41205041525420534554205553455294 \\
    --segment 40308f
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from typing import List, Optional

# Add project root to path so this file can be run from any working directory
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from galaxy.constants import COMMAND_BYTES, COMMANDS
from galaxy.protocol import build_block, validate_and_strip


DEFAULT_HOST    = '127.0.0.1'
DEFAULT_PORT    = 10000
DEFAULT_DELAY   = 0.05
DEFAULT_TIMEOUT = 2.0
SAMPLE_ACCOUNT  = '023499'
SAMPLE_EVENT    = 'ti23:42/id023/pi013/CG'
SAMPLE_ASCII    = ' PART SET USER'


def build_sia_block(command: str | int, payload: bytes = b'') -> bytes:
    """Build a valid Galaxy SIA block with checksum."""
    if isinstance(command, str):
        command = command.upper()
        if command not in COMMAND_BYTES:
            raise ValueError(f'Unknown SIA command: {command!r}')
        command_byte = COMMAND_BYTES[command]
    else:
        command_byte = command

    return build_block(command_byte, payload)


def parse_response(data: bytes) -> str:
    """Return the command name from a server response frame, or a hex fallback."""
    cmd_byte, _ = validate_and_strip(data)
    if cmd_byte is None:
        return f'(invalid frame: {data.hex()})'
    return COMMANDS.get(cmd_byte, f'UNKNOWN(0x{cmd_byte:02x})')


def parse_hex_segment(segment: str) -> bytes:
    """Convert a hex string to raw bytes."""
    normalized = segment.strip().replace(' ', '').replace('\\x', '')
    if len(normalized) % 2 != 0:
        raise ValueError(f'Hex segment has odd length: {segment!r}')
    return bytes.fromhex(normalized)


def send_and_receive(
    host: str,
    port: int,
    segments: List[bytes],
    delay: float,
    timeout: float,
    quiet: bool,
) -> List[tuple[bytes, str]]:
    """
    Send each segment and collect one response per segment.
    Returns a list of (sent_bytes, response_command_name) tuples.
    """
    results: List[tuple[bytes, str]] = []

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        for i, chunk in enumerate(segments, 1):
            sock.sendall(chunk)
            try:
                raw_resp = sock.recv(64)
                cmd_name = parse_response(raw_resp) if raw_resp else 'NO_RESPONSE'
            except socket.timeout:
                cmd_name = 'TIMEOUT'
            except OSError as e:
                cmd_name = f'ERROR({e})'

            results.append((chunk, cmd_name))

            if not quiet:
                ok = cmd_name == 'ACKNOWLEDGE'
                mark = 'OK' if ok else 'FAIL'
                print(f'  [{i}/{len(segments)}] {mark:4s}  {cmd_name}  '
                      f'({len(chunk)} bytes sent)')

            if delay > 0 and i < len(segments):
                time.sleep(delay)

    return results


def build_event_sequence(
    account_id: str,
    event_payload: str,
    event_command: str,
    ascii_text: Optional[str],
) -> List[bytes]:
    """Build ACCOUNT_ID → event → [ASCII] → END_OF_DATA block list."""
    blocks = [
        build_sia_block('ACCOUNT_ID', account_id.encode('ascii')),
        build_sia_block(event_command, event_payload.encode('ascii')),
    ]
    if ascii_text is not None:
        blocks.append(build_sia_block('ASCII', ascii_text.encode('ascii')))
    blocks.append(build_sia_block('END_OF_DATA', b''))
    return blocks


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Send Galaxy SIA packets to a SIA server.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--host',    default=DEFAULT_HOST, help='Server host (default: 127.0.0.1)')
    parser.add_argument('--port',    type=int, default=DEFAULT_PORT, help='Server port (default: 10000)')
    parser.add_argument('--delay',   type=float, default=DEFAULT_DELAY, help='Delay between segments in seconds.')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='Per-segment response timeout in seconds.')
    parser.add_argument('--account-id',  help='Account ID for ACCOUNT_ID block.')
    parser.add_argument('--new-event',   help='Payload for NEW_EVENT block (alarm).')
    parser.add_argument('--old-event',   help='Payload for OLD_EVENT block (non-alarm).')
    parser.add_argument('--ascii', dest='ascii_text', help='Optional payload for ASCII block.')
    parser.add_argument('--send-sample', action='store_true', help='Send the built-in sample message sequence.')
    parser.add_argument('--segment', action='append', default=[], help='Raw hex segment. Can be repeated.')
    parser.add_argument('--quiet', action='store_true', help='Suppress per-segment output.')

    args = parser.parse_args(argv)

    # --- Build segment list ---
    if args.send_sample:
        segments = build_event_sequence(SAMPLE_ACCOUNT, SAMPLE_EVENT, 'OLD_EVENT', SAMPLE_ASCII)

    elif args.segment:
        try:
            segments = [parse_hex_segment(s) for s in args.segment]
        except ValueError as e:
            parser.error(str(e))

    elif args.new_event or args.old_event:
        if not args.account_id:
            parser.error('--account-id is required when using --new-event or --old-event.')
        if args.new_event and args.old_event:
            parser.error('--new-event and --old-event are mutually exclusive.')
        event_payload = args.new_event or args.old_event
        event_command = 'NEW_EVENT' if args.new_event else 'OLD_EVENT'
        segments = build_event_sequence(args.account_id, event_payload, event_command, args.ascii_text)

    else:
        parser.error('Provide --send-sample, --segment, or --new-event / --old-event.')

    # --- Print header ---
    if not args.quiet:
        print('SIA Server Tester')
        print('-' * 40)
        print(f'  Target  : {args.host}:{args.port}')
        print(f'  Segments: {len(segments)}')
        print(f'  Delay   : {args.delay}s  Timeout: {args.timeout}s')
        print('-' * 40)

    # --- Send ---
    try:
        results = send_and_receive(args.host, args.port, segments, args.delay, args.timeout, args.quiet)
    except ConnectionRefusedError:
        print(f'ERROR: Connection refused — is the server running at {args.host}:{args.port}?',
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1

    # --- Summary ---
    ok_count = sum(1 for _, cmd in results if cmd == 'ACKNOWLEDGE')
    fail_count = len(results) - ok_count
    if not args.quiet:
        print('-' * 40)
        print(f'  Result: {ok_count}/{len(results)} acknowledged'
              + (f', {fail_count} failed' if fail_count else ''))

    return 0 if fail_count == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
