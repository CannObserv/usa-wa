"""Engine-test fixtures.

The fakes live in :mod:`clearinghouse_sync_powermap.testing` (importable, no
conftest-name collisions). This conftest only imports them so they register on
``Base.metadata`` for the test schema, and exposes a couple of fixtures.
"""

import pytest

from clearinghouse_sync_powermap.testing import (  # noqa: F401  (registers FakeEntity)
    FakeClient,
    FakeDescriptor,
    FakeEntity,
)


@pytest.fixture
def fake_descriptor() -> FakeDescriptor:
    return FakeDescriptor()
