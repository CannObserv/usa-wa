"""The root conftest's ``no_dns`` fixture (#569): name lookups refused, sockets left alone.

It exists beside the legislature package's ``no_network`` because that one also replaces
``socket.socket``, and an async test that runs first builds the session event loop under
it: the loop's own ``socketpair`` is refused before the test body starts.
"""

from __future__ import annotations

import socket

import pytest


@pytest.mark.usefixtures("no_dns")
def test_a_name_lookup_is_refused() -> None:
    with pytest.raises(OSError, match="no_dns"):
        socket.getaddrinfo("example.invalid", 443)


@pytest.mark.usefixtures("no_dns")
def test_a_socketpair_still_opens() -> None:
    """What an event loop needs for its self-pipe, and what ``no_network`` refuses."""
    left, right = socket.socketpair()
    left.close()
    right.close()
