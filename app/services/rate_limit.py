"""A small in-memory limiter for failed sign-in attempts.

Counts failures per key (email + client address) in a sliding window. It lives in the process, so it
resets on restart and is not shared between workers; that is enough to slow password guessing on a
single-process deployment. A multi-worker deployment needs a shared store instead.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.errors import TooManyRequestsError


class FailureLimiter:
    def __init__(self, max_failures: int = 5, window_seconds: float = 900.0, max_keys: int = 10_000) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self.max_keys = max_keys
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        entries = self._failures[key]
        while entries and now - entries[0] > self.window:
            entries.popleft()
        if not entries:
            self._failures.pop(key, None)
        return entries

    def check(self, key: str, *, now: float | None = None) -> None:
        """Raise ``TooManyRequestsError`` if the key has used up its failures; otherwise do nothing."""
        now = time.monotonic() if now is None else now
        with self._lock:
            entries = self._prune(key, now)
            if len(entries) >= self.max_failures:
                wait = max(1, int(self.window - (now - entries[0])) + 1)
                raise TooManyRequestsError(f"Too many failed sign-in attempts. Try again in {max(1, wait // 60)} minutes.",
                                           retry_after=wait)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._failures) >= self.max_keys and key not in self._failures:
                self._failures.pop(next(iter(self._failures)))  # bound memory: forget the oldest key
            self._failures[key].append(now)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


login_limiter = FailureLimiter()
