"""
Galaxy SIA Protocol Payload Parser

This module is responsible for parsing the *payloads* of valid Galaxy SIA
message blocks. It does not handle protocol framing (length, command, checksums).
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict

log = logging.getLogger(__name__)

@dataclass
class GalaxyEvent:
    """Structured data for a complete Galaxy SIA event."""
    # Raw Payloads for debugging
    account_payload: Optional[bytes] = None
    data_payload: Optional[bytes] = None
    ascii_payload: Optional[bytes] = None

    # Parsed from Account Payload
    account: Optional[str] = None
    site_name: Optional[str] = None

    # Block type — set by parse_galaxy_event from the command byte
    is_alarm: Optional[bool] = None   # True = NEW_EVENT (0x4E), False = OLD_EVENT (0x4F)

    # Parsed from event data payload (NEW_EVENT / OLD_EVENT)
    # SIA field codes and their default value widths (4 chars unless noted)
    date: Optional[str] = None            # da= date
    time: Optional[str] = None            # ti= time
    subscriber_id: Optional[str] = None   # id= subscriber ID
    area_id: Optional[str] = None         # ri= area ID
    peripheral_id: Optional[str] = None   # pi= peripheral ID
    automated_id: Optional[str] = None    # ai= automated ID
    phone_id: Optional[str] = None        # ph= telephone ID
    sia_level: Optional[str] = None       # lv= SIA level
    value: Optional[str] = None           # va= SIA value
    sia_subscriber_id: Optional[str] = None  # ss= SIA subscriber ID
    route_group: Optional[str] = None     # rg= route group (2 chars)
    sia_path: Optional[str] = None        # pt= SIA path (3 chars)
    event_code: Optional[str] = None
    event_description: Optional[str] = None
    zone: Optional[str] = None

    # Parsed from ASCII Payload
    action_text: Optional[str] = None

def decode_unknown_text(data: bytes, char_map: dict) -> str:
    """
    Decodes panel text using CP437 with overrides for characters that differ
    from standard CP437 on this panel.
    char_map keys are raw byte values (int), values are Unicode replacements.
    """
    try:
        # Build a string-to-string replacement map by decoding each key byte through CP437
        str_map = {
            bytes([k]).decode("cp437"): v
            for k, v in char_map.items()
        }
        # Decode the full data using CP437
        text = data.decode("cp437", errors="replace")

        # Replace CP437 results with correct characters
        for cp437_char, correct_char in str_map.items():
            text = text.replace(cp437_char, correct_char)

        return text.strip()

    except Exception as e:
        log.warning("Could not decode text data: %s", e)
        return data.decode("utf-8", errors="replace").strip()

def parse_account_payload(payload: bytes, event: GalaxyEvent):
    """Parses the clean payload of an ACCOUNT_ID block."""
    event.account_payload = payload
    event.account = payload.decode('utf-8', errors='ignore')
    log.debug("Parsed account: '%s'", event.account)

def parse_data_payload(payload: bytes, event: GalaxyEvent, event_code_descriptions: Dict):
    """
    Parses the clean payload of a NEW_EVENT (alarm) or OLD_EVENT (non-alarm) block.

    Payload is '/'-delimited sections. Lowercase 2-char prefixes are data modifiers;
    uppercase 2-char starts are the event code (with optional zone suffix).
    All field values default to 4 characters wide unless noted (rg=2, pt=3).

    Known field codes:
      da=date  ti=time  id=subscriber  ri=area  pi=peripheral  ai=automated
      ph=phone  lv=level  va=value  ss=sia_subscriber  rg=route_group(2)  pt=path(3)

    Examples:
      'ti11:45/id001/pi010/CL'
      'ti11:46/BA1011'
      'da0604/ti1145/id0001/ri0001/pi0010/CL'
    """
    event.data_payload = payload
    data_str = payload.decode('utf-8', errors='ignore')

    sections = data_str.split('/')
    if not sections:
        return

    for section in sections:
        if not section:
            continue
        if section[:2].islower():
            # Data modifier section
            val = section[2:]
            if section.startswith('da'):
                event.date = val
                log.debug("Parsed date: '%s'", val)
            elif section.startswith('ti'):
                event.time = val
                log.debug("Parsed time: '%s'", val)
            elif section.startswith('id'):
                event.subscriber_id = val.lstrip('0') or '0'
                log.debug("Parsed subscriber_id: '%s'", event.subscriber_id)
            elif section.startswith('ri'):
                event.area_id = val.lstrip('0') or '0'
                log.debug("Parsed area_id: '%s'", event.area_id)
            elif section.startswith('pi'):
                event.peripheral_id = val.lstrip('0') or '0'
                log.debug("Parsed peripheral_id: '%s'", event.peripheral_id)
            elif section.startswith('ai'):
                event.automated_id = val.lstrip('0') or '0'
                log.debug("Parsed automated_id: '%s'", event.automated_id)
            elif section.startswith('ph'):
                event.phone_id = val
                log.debug("Parsed phone_id: '%s'", val)
            elif section.startswith('lv'):
                event.sia_level = val
                log.debug("Parsed sia_level: '%s'", val)
            elif section.startswith('va'):
                event.value = val.lstrip('0') or '0'
                log.debug("Parsed value: '%s'", event.value)
            elif section.startswith('ss'):
                event.sia_subscriber_id = val
                log.debug("Parsed sia_subscriber_id: '%s'", val)
            elif section.startswith('rg'):
                event.route_group = val
                log.debug("Parsed route_group: '%s'", val)
            elif section.startswith('pt'):
                event.sia_path = val
                log.debug("Parsed sia_path: '%s'", val)
            else:
                log.warning("Unknown data section modifier '%s' in payload: %r", section, payload)
        elif section[:2].isupper():
            # Event code section: 2 uppercase letters + optional 1-4 digit zone.
            # The trailing group captures anything left over so we can warn
            # about it instead of silently ignoring malformed data.
            ec_match = re.match(r'([A-Z]{2})(\d{1,4})?(.*)$', section)
            if ec_match:
                event.event_code = ec_match.group(1)
                log.debug("Parsed event_code: '%s'", event.event_code)
                event.event_description = event_code_descriptions.get(event.event_code, "Unknown")
                log.debug("Mapped event description: '%s'", event.event_description)
                if ec_match.group(2):
                    event.zone = ec_match.group(2).lstrip('0') or '0'
                    log.debug("Parsed zone: '%s'", event.zone)
                if ec_match.group(3):
                    log.warning("Trailing data after event code in section '%s': '%s'",
                                section, ec_match.group(3))
            else:
                log.warning("Could not parse event code from section: %s", section)
        else:
            log.warning("Unknown data section '%s' in payload: %r", section, payload)

def parse_ascii_payload(payload: bytes, event: GalaxyEvent, char_map: Dict[int, str]):
    """Parses the clean payload of an ASCII block."""
    event.ascii_payload = payload
    event.action_text = decode_unknown_text(payload, char_map)
    log.debug("Parsed action_text: '%s'", event.action_text)

def parse_galaxy_event(blocks: List[Dict], account_sites: Dict, 
                      char_map: Dict, event_code_descriptions: Dict) -> GalaxyEvent:
    """
    Parses a chunk of valid blocks into a GalaxyEvent object.
    
    Args:
        blocks: A list of dicts, each with 'command' and a clean 'payload'.
        account_sites: Dict mapping account numbers to site names.
        char_map: Custom character mapping dictionary.
        event_code_descriptions: Dict mapping event codes to descriptions.
        
    Returns:
        A populated GalaxyEvent object.
    """
    event = GalaxyEvent()
    
    for block in blocks:
        command = block['command']
        payload = block['payload']
        
        if command == 'ACCOUNT_ID':
            parse_account_payload(payload, event)
            if event.account:
                event.site_name = account_sites.get(event.account, event.account)

        elif command == 'NEW_EVENT':
            parse_data_payload(payload, event, event_code_descriptions)
            event.is_alarm = True

        elif command == 'OLD_EVENT':
            parse_data_payload(payload, event, event_code_descriptions)
            event.is_alarm = False

        elif command == 'ASCII':
            parse_ascii_payload(payload, event, char_map)

        else:
            log.warning("Unknown command '%s' passed to parser. Payload: %r", command, payload)

    return event


def split_event_chunks(blocks: List[Dict]) -> List[List[Dict]]:
    """
    Splits a flat list of validated blocks into per-event chunks.

    Galaxy panels can send several events in one connection; each new
    event starts with another ACCOUNT_ID block. Extracted from the
    connection handler so it can be unit tested.
    """
    chunks: List[List[Dict]] = []
    current: List[Dict] = []
    for block in blocks:
        if block['command'] == 'ACCOUNT_ID' and current:
            chunks.append(current)
            current = [block]
        else:
            current.append(block)
    if current:
        chunks.append(current)
    return chunks

