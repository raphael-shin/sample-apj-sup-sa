"""In-memory circuit breaker for Bedrock provider health.

State machine per Bedrock region:

    CLOSED  -- record_failure --> OPEN
    OPEN    -- timer expired   --> HALF (next call probes)
    HALF    -- record_success  --> CLOSED
    HALF    -- record_failure  --> OPEN (timer reset)

Only provider-outage and throttle failures should call record_failure.
Client-bug failures (ValidationException, AccessDenied, ...) do NOT trip
the breaker because the Bedrock service itself is still healthy.

State is per-task in-memory. With multiple ECS tasks behind ALB, each task
discovers Bedrock outage independently on its first failed request.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF = "half"


@dataclass
class _RegionState:
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0


class CircuitBreaker:
    """Per-region breaker that decides whether to call Bedrock."""

    def __init__(self, open_seconds: float = 300.0) -> None:
        self._open_seconds = open_seconds
        self._states: dict[str, _RegionState] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def _get(self, region: str) -> _RegionState:
        return self._states.setdefault(region, _RegionState())

    def allow_bedrock(self, region: str) -> bool:
        """Return True if the next request for this region should hit Bedrock.

        Side effect: transitions OPEN -> HALF when the open window has expired,
        so the caller is implicitly the probe.
        """
        with self._lock:
            entry = self._get(region)
            if entry.state in (BreakerState.CLOSED, BreakerState.HALF):
                return True
            if self._now() - entry.opened_at >= self._open_seconds:
                entry.state = BreakerState.HALF
                return True
            return False

    def record_success(self, region: str) -> None:
        with self._lock:
            entry = self._get(region)
            entry.state = BreakerState.CLOSED
            entry.opened_at = 0.0

    def record_failure(self, region: str) -> None:
        with self._lock:
            entry = self._get(region)
            entry.state = BreakerState.OPEN
            entry.opened_at = self._now()

    def state(self, region: str) -> BreakerState:
        with self._lock:
            return self._get(region).state
