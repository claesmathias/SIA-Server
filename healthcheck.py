#!/usr/bin/env python3
"""
Docker HEALTHCHECK for the Galaxy SIA Server.

The server speaks a raw TCP protocol (no HTTP), so health is checked by
opening a TCP connection to the main SIA event port and closing it
immediately without sending data.

A successful connect proves the asyncio event loop is alive and the
listener is bound. sia-server.py treats an empty read (connect-then-close)
as a clean disconnect and logs it at DEBUG level, so this check produces
no warning noise in the server log.

The port defaults to 10000 (sia-server.conf's [SIA-Server] LISTEN_PORT
default). If you changed LISTEN_PORT, set the HEALTHCHECK_PORT environment
variable on the container to match.
"""
import os
import socket
import sys

HOST = "127.0.0.1"
PORT = int(os.environ.get("HEALTHCHECK_PORT", "10000"))
TIMEOUT = 3.0


def main() -> int:
    try:
        with socket.create_connection((HOST, PORT), timeout=TIMEOUT):
            return 0
    except OSError as e:
        print(f"Healthcheck failed: cannot connect to {HOST}:{PORT}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
