"""Tests for healthcheck.py — the Docker HEALTHCHECK probe.

Uses real TCP sockets (matching the style of test_sia_server.py) rather
than mocking, so these exercise the exact connect-and-close path Docker
will invoke.
"""
import socket
import threading

import healthcheck


def _free_port() -> int:
    """Bind to port 0 to let the OS pick a free port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Listener:
    """A minimal TCP listener that accepts and immediately closes connections."""

    def __init__(self, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


class TestMainAgainstLiveListener:
    def test_returns_zero_when_port_accepts_connections(self, monkeypatch):
        port = _free_port()
        listener = _Listener(port)
        try:
            monkeypatch.setenv("HEALTHCHECK_PORT", str(port))
            assert healthcheck.main() == 0
        finally:
            listener.stop()

    def test_returns_one_when_port_closed(self, monkeypatch):
        port = _free_port()  # nothing listening on this port
        monkeypatch.setenv("HEALTHCHECK_PORT", str(port))
        assert healthcheck.main() == 1

    def test_error_message_includes_host_and_port(self, monkeypatch, capsys):
        port = _free_port()
        monkeypatch.setenv("HEALTHCHECK_PORT", str(port))
        healthcheck.main()
        captured = capsys.readouterr()
        assert healthcheck.HOST in captured.err
        assert str(port) in captured.err


class TestDefaultPort:
    def test_uses_default_port_when_env_var_unset(self, monkeypatch):
        """Falls back to DEFAULT_PORT when HEALTHCHECK_PORT isn't set.

        Patches DEFAULT_PORT to a free ephemeral port rather than binding
        to the real default (10000), to avoid colliding with an actual
        SIA server that might be running on the test machine.
        """
        monkeypatch.delenv("HEALTHCHECK_PORT", raising=False)
        port = _free_port()
        monkeypatch.setattr(healthcheck, "DEFAULT_PORT", port)
        listener = _Listener(port)
        try:
            assert healthcheck.main() == 0
        finally:
            listener.stop()

    def test_default_port_constant_matches_sia_server_conf_default(self):
        assert healthcheck.DEFAULT_PORT == 10000
