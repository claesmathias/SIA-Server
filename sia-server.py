#!/usr/bin/env python3
"""
Galaxy SIA Server
Receives, validates, and parses proprietary SIA protocol messages from
Honeywell Galaxy Flex alarm systems. It sends notifications via Apprise to supported endpoints.

This server is configured via 'sia-server.conf' and 'configuration.py'.
"""
from __future__ import annotations

import os
import argparse
import asyncio
import logging
import logging.handlers
import sys
import signal
import functools
from queue import Queue

# --- SCRIPT INITIALIZATION ---
# Parse command line arguments FIRST, before anything else
parser = argparse.ArgumentParser(description='Galaxy SIA Notification Server')
parser.add_argument(
    '--config',
    default='sia-server.conf',
    help='Path to configuration file (default: sia-server.conf)'
)
args = parser.parse_args()

from configuration import load_logging_config, load_full_config

# Load and validate all configuration from files.
# This single 'config' object now holds all settings for the application.
logging_config = load_logging_config(args.config)

# Define the logging setup function.
def setup_logging(logging_config):
    """Configure logging based on the loaded logging config object."""
    log = logging.getLogger() 
    if log.handlers:
        for handler in log.handlers[:]:
            log.removeHandler(handler)

    log.setLevel(getattr(logging, logging_config.LOG_LEVEL, 'INFO'))

    handler = None

    if logging_config.LOG_TO_SYSLOG:
        if sys.platform == "win32":
            try:
                import win32evtlogutil
                import win32evtlog
                win32evtlogutil.AddSourceToRegistry("SIA-Server", sys.executable)
                handler = logging.handlers.NTEventLogHandler("SIA-Server")
            except ImportError:
                print("WARNING: 'pywin32' not installed. Falling back to screen logging.",
                      file=sys.stderr)
            except Exception as e:
                print("WARNING: Failed to initialize Windows Event Log: %s" % e,
                      file=sys.stderr)
        else:
            try:
                handler = logging.handlers.SysLogHandler(
                    address=logging_config.SYSLOG_SOCKET,
                    facility=logging_config.SYSLOG_FACILITY
                )
            except Exception as e:
                print("WARNING: Could not connect to syslog at %s: %s. Falling back to screen."
                      % (logging_config.SYSLOG_SOCKET, e), file=sys.stderr)

    elif logging_config.LOG_TO_FILE:
        max_bytes = logging_config.LOG_MAX_MB * 1024 * 1024
        handler = logging.handlers.RotatingFileHandler(
            logging_config.LOG_FILE,
            maxBytes=max_bytes,
            backupCount=logging_config.LOG_BACKUP_COUNT
        )

    if handler is None:
        handler = logging.StreamHandler(sys.stderr)

    if isinstance(handler, (logging.handlers.SysLogHandler,
                            logging.handlers.NTEventLogHandler)):
        formatter = logging.Formatter(logging_config.SYSLOG_FORMAT,
                                      datefmt=logging_config.LOG_DATE_FORMAT)
    else:
        formatter = logging.Formatter(logging_config.LOG_FORMAT,
                                      datefmt=logging_config.LOG_DATE_FORMAT)

    handler.setFormatter(formatter)
    log.addHandler(handler)

    if isinstance(handler, logging.handlers.NTEventLogHandler):
        log.info("Logging configured to write to Windows Event Log.")
    elif isinstance(handler, logging.handlers.SysLogHandler):
        log.info("Logging configured to write to system log (Syslog).")
    elif isinstance(handler, logging.handlers.RotatingFileHandler):
        log.info("Logging configured to write to file: %s", logging_config.LOG_FILE)
    else:
        log.info("Logging configured to write to screen (console).")

    return log

# Set up logging immediately after loading logging config.
log = setup_logging(logging_config)
log.info("Logging configured successfully.")
log.info("Using configuration file: %s", args.config)

# Now load the full configuration WITH logging available ---
config = load_full_config(args.config)

# Now, import the rest of our modules.
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    log.info("Using uvloop for event loop.")
except ImportError:
    log.info("uvloop not found, using standard asyncio event loop.")
    pass

# --- Optional Encryption Support ---
ENCRYPTION_AVAILABLE = False
START_ENC_HEADER = b'\x05\x01'
CryptoContext = None
do_handshake = None
try:
    from galaxy.encryption import do_handshake, CryptoContext
    ENCRYPTION_AVAILABLE = True
    log.info("INFO: Encryption modules loaded. Encrypted SIA sessions are supported.")
except ModuleNotFoundError:
    log.info("Encryption modules not found. Encrypted sessions will be rejected.")
except ImportError:
    log.info("Encryption modules failed to import. Encrypted sessions will be rejected.")
# ---

from galaxy.parser import parse_galaxy_event, split_event_chunks
from galaxy.protocol import validate_and_strip, build_block, extract_blocks
from notification import NotificationDispatcher, enqueue_notification
from galaxy.constants import COMMANDS, COMMAND_BYTES, EVENT_CODE_DESCRIPTIONS

# --- END INITIALIZATION ---


async def build_and_send(writer, command: str, payload: bytes = b'', crypto: CryptoContext | None = None):
    """Builds and sends a valid Galaxy message block."""
    final_message = build_block(COMMAND_BYTES[command], payload)

    if crypto:
        log.debug("Encrypting outgoing command: %s", command)
        final_message = crypto.encrypt(final_message)
    
    writer.write(final_message)
    await writer.drain()
    log.debug("Sent Command: %s, Raw: %r", command, final_message)

async def policy_reject(writer, crypto=None):
    """
    Handles a connection rejection according to the configured REJECT_POLICY.
    'respond' - Sends a SIA REJECT frame to the client.
    'drop'    - Silently closes without sending anything.
    """
    if config.REJECT_POLICY == 'respond':
        await build_and_send(writer, 'REJECT', crypto=crypto)
    log.debug("Connection rejected (policy: %s)", config.REJECT_POLICY)
    

async def handle_connection(notification_queue: Queue, reader, writer):
    """Handle an incoming SIA connection."""
    addr = writer.get_extra_info('peername')
    log.debug("Connection from %r", addr)

    crypto = None  # This will hold our CryptoContext object if the session is encrypted
    account_validated = False
    valid_blocks = []
    buffer = bytearray()  # Reassembly buffer: TCP reads are not message-aligned
    end_of_data = False

    try:
        while not end_of_data:
            data = await reader.read(1024)
            if not data:
                log.debug("Connection closed by peer")
                break
            # --- encryption detection ---
            if data.startswith(START_ENC_HEADER):
                if ENCRYPTION_AVAILABLE:
                    log.debug("Encrypted header detected from %r", addr)
                    crypto = await do_handshake(reader, writer, data, log)
                    if crypto is None:
                        log.warning("Encryption handshake failed from %r, closing connection", addr)
                        await policy_reject(writer, crypto=None)
                        return
                    log.info("Encrypted session established from %r", addr)
                    # Handshake successful, now wait for the first real SIA message.
                    data = await reader.read(1024)
                    if not data:
                        log.info("Connection closed after handshake")
                        return
                else:
                    # This block runs if encryption is detected but not supported.
                    log.error("="*60)
                    log.error("ENCRYPTION DETECTED - UNSUPPORTED")
                    log.error("The panel at IP address '%s' has encryption enabled.", addr[0])
                    log.error("Required modules for encryption are missing.")
                    log.error("Closing connection to stop panel retries.")
                    log.error("="*60)
                    return

            if crypto:
                data = crypto.decrypt(data)

            # A single read may contain several blocks, or only part of one.
            # Accumulate bytes and process every complete block in order.
            buffer.extend(data)
            blocks, in_sync = extract_blocks(buffer)

            for block in blocks:
                command_byte, payload = validate_and_strip(block)

                if command_byte is None:
                    if config.REJECT_POLICY == 'respond':  # only print warning if we respond
                        log.warning("Invalid frame from %r - rejected.", addr)
                    log.debug("Raw: %r", block)
                    await policy_reject(writer, crypto=crypto)
                    continue

                command_name = COMMANDS.get(command_byte, f'UNKNOWN(0x{command_byte:02x})')
                log.debug("Received Command: %s, Payload: %r", command_name, payload)

                if not account_validated and command_name != 'ACCOUNT_ID':
                    log.warning("Protocol violation from %r: expected ACCOUNT_ID, got '%s'. Rejecting.",
                                addr, command_name)
                    await policy_reject(writer, crypto=crypto)
                    return

                # --- ACCOUNT POLICY ENFORCEMENT ---
                # Validate account_id if according to policy
                if command_name == 'ACCOUNT_ID':
                    account_number = payload.decode(errors='ignore')

                    # Look up the policy. Fall back to 'default', then to 'yes'.
                    policy = config.ACCOUNT_POLICIES.get(
                        account_number,
                        config.ACCOUNT_POLICIES.get('default', 'yes')
                    )

                    is_encrypted = crypto is not None
                    log.debug("Account '%s' has policy '%s'. Session is encrypted: %s",
                              account_number, policy, is_encrypted)
                    # Policy: 'no' - This account is completely disabled.
                    if policy == 'no':
                        log.warning("POLICY: Account '%s' is DISABLED. Rejecting connection.", account_number)
                        await policy_reject(writer, crypto=crypto)
                        return
                    # Policy: 'secure' - This account requires an encrypted session.
                    if policy == 'secure' and not is_encrypted:
                        log.warning("POLICY: Account '%s' requires ENCRYPTED connection but received PLAINTEXT. Rejecting.", account_number)
                        await policy_reject(writer, crypto=crypto)
                        return
                    # If we reach here, the policy is satisfied.
                    account_validated = True
                    log.debug("POLICY: Account '%s' policy satisfied.", account_number)

                if command_name != 'END_OF_DATA':
                    valid_blocks.append({'command': command_name, 'payload': payload})
                await build_and_send(writer, 'ACKNOWLEDGE', crypto=crypto)

                if command_name == 'END_OF_DATA':
                    log.debug("End of data received, processing sequence.")
                    end_of_data = True
                    break

            if not in_sync:
                # The stream contains an implausible length byte - we have
                # lost framing and cannot recover. Reject and close.
                log.warning("Unrecoverable framing error from %r - closing.", addr)
                log.debug("Buffer: %r", bytes(buffer))
                await policy_reject(writer, crypto=crypto)
                return

        if not valid_blocks:
            return

        event_chunks = split_event_chunks(valid_blocks)

        log.info("Found %d event(s) in connection from %s", len(event_chunks), addr[0])
        for i, chunk in enumerate(event_chunks, 1):
            log.debug("--- Processing Event %d of %d ---", i, len(event_chunks))
            
            event = parse_galaxy_event(
                chunk,
                config.ACCOUNT_SITES,
                config.UNKNOWN_CHAR_MAP,
                EVENT_CODE_DESCRIPTIONS
            )
            
            log.info("Site: %s (Account: %s)", event.site_name, event.account)

            if not event.event_code and not event.action_text:
                log.debug("--- Event %d skipped (heartbeat/poll, no event data) ---", i)
                continue

            description = event.action_text or event.event_description
            log.info("Event: %s (%s) alarm=%s", event.event_code, description, event.is_alarm)

            enqueue_notification(event, notification_queue)

            log.debug("--- Event %d complete ---", i)

    except (ConnectionResetError, BrokenPipeError):
        log.debug("Client disconnected abruptly (%r)", addr)
        return

    except asyncio.IncompleteReadError:
        log.debug("Client closed connection during read (%r)", addr)
        return

    except Exception as e:
        log.error("Error in connection handler: %s", e, exc_info=True)
    
    finally:
        log.debug("Closing connection from %r", addr)
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError, OSError):
            log.debug("The connection was closed ugly by the client (%r)", addr)
            pass  # Client already closed the connection
        except Exception as e:
            log.error("Error closing connection: %s", e)

async def monitor_subprocess(process, name):
    """Monitors a subprocess, parses its log level, and logs its output."""
    log.info("Monitoring subprocess '%s' (PID: %d)", name, process.pid)
    LEVEL_MAP = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}
    async def log_stream(stream, default_level):
        while not stream.at_eof():
            line = await stream.readline()
            if line:
                line_str = line.decode(errors='replace').strip()
                parts = line_str.split(':', 1)
                if len(parts) == 2 and parts[0] in LEVEL_MAP:
                    level_name, msg = parts[0], parts[1].strip()
                    log_level = LEVEL_MAP[level_name]
                else:
                    msg, log_level = line_str, default_level
                log.log(log_level, "[%s] %s", name, msg)
    await asyncio.gather(log_stream(process.stdout, logging.INFO), log_stream(process.stderr, logging.ERROR))
    await process.wait()
    log.warning("Subprocess '%s' (PID: %d) has exited with code %d.", name, process.pid, process.returncode)

async def start_servers(notification_queue: Queue):
    """Starts the main SIA server and launches the IP Check server as a subprocess."""
    
    try:
        handler_with_queue = functools.partial(handle_connection, notification_queue)
        # --- Start the main SIA Event Server ---
        sia_server = await asyncio.start_server(
            handler_with_queue, config.LISTEN_ADDR, config.LISTEN_PORT
        )
        sia_addrs = ', '.join(str(sock.getsockname()) for sock in sia_server.sockets)
        log.info('='*60)
        log.info('Galaxy SIA Event Server Started')
        log.info('Listening for events on: %s', sia_addrs)
    except OSError as e:
        if "Address already in use" in str(e):
            log.critical("STARTUP FAILED: The port %d is already in use.", config.LISTEN_PORT)
        elif "Cannot assign requested address" in str(e) or "could not bind" in str(e):
            log.critical("STARTUP FAILED: The IP address '%s' is not valid for this machine.", config.LISTEN_ADDR)
            log.critical("Please use '0.0.0.0' or a specific IP address that this server owns.")
        elif "getaddrinfo failed" in str(e):
            log.critical("STARTUP FAILED: The address '%s' is not a valid IP address or hostname.", config.LISTEN_ADDR)
            log.critical("Please check for typos in your sia-server.conf file.")
        else:
            log.critical("A critical OS error occurred starting the SIA server: %s", e)
        
        log.critical("="*60)
        # We must return here to stop the program from continuing.
        raise # this triggers the OSError in the main loop

    # --- Launch the optional IP Check Server as a Subprocess ---
    ip_check_process = None
    ip_check_monitor_task = None
    if config.IP_CHECK_ENABLED:
        try:
            command = [sys.executable, 'ip_check.py', '--config', args.config]
            log.info("Launching IP Check server as a subprocess: %s", " ".join(command))
            ip_check_process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            ip_check_monitor_task = asyncio.create_task(monitor_subprocess(ip_check_process, 'ip_check.py'))
        except Exception as e:
            log.error("Failed to launch IP Check server subprocess: %s", e)
    
    log.info('='*60)

    # Prefer loop-level signal handlers so the finally-block below (which
    # terminates the IP Check subprocess) always runs on SIGINT/SIGTERM.
    loop = asyncio.get_running_loop()
    serve_task = asyncio.ensure_future(sia_server.serve_forever())
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: (log.info("Received signal %s, shutting down...", s),
                                    serve_task.cancel()))
        except (NotImplementedError, RuntimeError):
            pass  # Windows: fall back to the signal.signal() handlers in main()

    # Run the main SIA server forever
    try:
        await serve_task
    except asyncio.CancelledError:
        log.info("Server shutdown requested.")
    finally:
        # When the main server is shut down, also terminate the subprocess
        if ip_check_process and ip_check_process.returncode is None:
            log.info("Terminating IP Check server subprocess...")
            ip_check_process.terminate()
            await ip_check_process.wait()
            log.info("IP Check subprocess terminated.")

def handle_shutdown(signum, frame):
    # Fallback handler (Windows / loops without add_signal_handler support).
    log.info("Received shutdown signal (%d), stopping server...", signum)
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    logging.info(
        "Starting Galaxy SIA Server  | version=%s | build=%s | commit=%s",
        os.getenv("APP_VERSION", "unknown"),
        os.getenv("BUILD_NUMBER", "local"),
        os.getenv("COMMIT_SHA", "dev")[:7]
    )

    notification_queue = Queue(maxsize=config.MAX_QUEUE_SIZE)
    dispatcher = NotificationDispatcher(
        notification_queue,
        config.APPRISE_TOPICS,
        config.EVENT_PRIORITIES,
        config.DEFAULT_PRIORITY,
        config.MAX_RETRIES,
        config.MAX_RETRY_TIME
    )
    dispatcher.start()
    
    exit_code = 0 # Assume success
    try:
        asyncio.run(start_servers(notification_queue))
    except (KeyboardInterrupt, SystemExit):
        log.info("Server stopped")
    except OSError as e:
        # OSError raised by start_servers, no need for additional logging.
        exit_code = 1
    except Exception as e:
        # This will now only catch very unexpected errors.
        log.critical("A critical server error occurred: %s", e, exc_info=True)
        exit_code = 1
    finally:
        # This block ensures the dispatcher is stopped when the server exits for any reason.
        log.info("Shutting down notification dispatcher...")
        dispatcher.stop()   # Signals the thread's loop to exit
        dispatcher.join()   # Waits for the thread to finish cleanly
        log.info("Notification dispatcher stopped.")
    
    sys.exit(exit_code)
    
if __name__ == '__main__':
    main()
