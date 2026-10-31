"""Deny sockets, with one scoped allowance for the stdlib self-pipe."""

from contextvars import ContextVar
from functools import wraps
import socket


class NetworkBlocked(RuntimeError):
    pass


def install_guard(monkeypatch):
    allowance = ContextVar("demo_socketpair_allowance", default=0)
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    socketpair = socket.socketpair

    def guarded(original):
        @wraps(original)
        def call(sock, address):
            loopback = isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}
            if allowance.get() == 1 and loopback:
                allowance.set(0)
                return original(sock, address)
            raise NetworkBlocked("Demo tests forbid outgoing socket connections")
        return call

    @wraps(socketpair)
    def internal_pair(*args, **kwargs):
        # Python 3.13 asyncio uses socket.socketpair on both platforms. Its
        # Windows fallback reaches connect synchronously inside this call.
        token = allowance.set(1)
        try:
            return socketpair(*args, **kwargs)
        finally:
            allowance.reset(token)

    def create_connection(*args, **kwargs):
        raise NetworkBlocked("Demo tests forbid outgoing socket connections")

    monkeypatch.setattr(socket.socket, "connect", guarded(connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(connect_ex))
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "socketpair", internal_pair)
