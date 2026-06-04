"""Exponential backoff for outbox retries.

Pure functions so the schedule is unit-testable without a clock. The engine
passes ``now`` explicitly and stamps ``next_attempt_at = now + backoff(attempts)``.
"""

from datetime import datetime, timedelta

#: First-retry delay.
BASE_DELAY = timedelta(seconds=60)
#: Ceiling — retries never wait longer than this.
MAX_DELAY = timedelta(hours=1)


def backoff(attempts: int) -> timedelta:
    """Delay before the next attempt, given the number of attempts so far.

    ``attempts`` is 1 on the first failure. Doubles each time, capped at
    :data:`MAX_DELAY`. Non-positive inputs collapse to :data:`BASE_DELAY`.
    """
    if attempts < 1:
        return BASE_DELAY
    # Cap the exponent so a huge attempt count can't overflow timedelta's C int;
    # 2**20 * BASE_DELAY already dwarfs MAX_DELAY, so the min() still clamps.
    exp = min(attempts - 1, 20)
    scaled = BASE_DELAY * (2**exp)
    return min(scaled, MAX_DELAY)


def next_attempt_at(now: datetime, attempts: int) -> datetime:
    """Absolute time of the next retry."""
    return now + backoff(attempts)
