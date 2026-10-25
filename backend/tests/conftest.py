"""All automated tests are offline; live probes are separate manual scripts."""
import socket

import pytest


@pytest.fixture(autouse=True)
def deny_network_connections(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Network connections are prohibited in automated tests')

    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
