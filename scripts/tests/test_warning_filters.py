"""The suite fails on a deprecation, whichever class it arrives in (#544).

``error::DeprecationWarning`` is how #504 caught sqlalchemy 2.1's deprecation. But
not every deprecation is a ``DeprecationWarning``:

* ``PendingDeprecationWarning`` and ``FutureWarning`` sit beside it, not under it.
* starlette, fastapi, uvicorn and cryptography each subclass their deprecation
  warning from ``UserWarning``. Python hides ``DeprecationWarning`` by default
  outside ``__main__``, and these libraries want theirs seen. starlette 1.3's
  "install httpx2" notice for ``starlette.testclient`` is one of these, and the old
  filter let it through.

So ``filterwarnings`` in the root ``pyproject.toml`` turns all four stdlib
categories into errors. Each case below emits its warning inside a test, under the
live filters: a deprecation passes only if the filter raised it, and pytest's own
out-of-band warnings pass only if it did not.
"""

from __future__ import annotations

import warnings

import pytest
from fastapi.exceptions import FastAPIDeprecationWarning
from starlette.exceptions import StarletteDeprecationWarning
from uvicorn.config import UvicornDeprecationWarning


@pytest.mark.parametrize(
    "category",
    [
        DeprecationWarning,
        PendingDeprecationWarning,
        FutureWarning,
        StarletteDeprecationWarning,
        FastAPIDeprecationWarning,
        UvicornDeprecationWarning,
    ],
)
def test_a_deprecation_fails_the_suite(category: type[Warning]) -> None:
    with pytest.raises(category):
        warnings.warn("#544 tripwire", category, stacklevel=1)


class _NewDependencyWarning(UserWarning):
    """A warning class no library here defines yet: what tomorrow's dependency ships."""


def test_any_user_warning_subclass_fails_the_suite() -> None:
    with pytest.raises(_NewDependencyWarning):
        warnings.warn("#544 tripwire", _NewDependencyWarning, stacklevel=1)


def _pytest_warnings(*, deprecations: bool) -> list[type[Warning]]:
    """pytest's exported warning classes, its own deprecations or everything else."""
    found = [
        obj
        for obj in vars(pytest).values()
        if isinstance(obj, type) and issubclass(obj, pytest.PytestWarning)
    ]
    return sorted(
        (c for c in found if issubclass(c, pytest.PytestDeprecationWarning) == deprecations),
        key=lambda c: c.__name__,
    )


@pytest.mark.parametrize("category", _pytest_warnings(deprecations=False), ids=lambda c: c.__name__)
def test_pytests_own_warnings_stay_warnings(category: type[Warning]) -> None:
    """PytestWarning subclasses UserWarning, but stays what it was before #544.

    Several land out of band, where an error is an INTERNALERROR or fails an unrelated
    test: a config warning under --noconftest, an unwritable cache at session end, an
    unraisable or thread exception wherever the collector or thread reaches.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.warn("#544 not a deprecation", category, stacklevel=1)
    assert [w.category for w in caught] == [category]


@pytest.mark.parametrize("category", _pytest_warnings(deprecations=True), ids=lambda c: c.__name__)
def test_pytests_own_deprecations_still_fail_the_suite(category: type[Warning]) -> None:
    with pytest.raises(category):
        warnings.warn("#544 tripwire", category, stacklevel=1)
