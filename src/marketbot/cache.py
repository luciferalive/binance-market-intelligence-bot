"""Small thread-safe TTL cache with one lock per key, so concurrent requests share one fetch."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_entries: int = 200):
        self._clock = clock
        self._max_entries = max_entries
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _evict(self) -> None:
        """Drop the oldest idle entries so arbitrary keys cannot grow memory forever."""
        excess = len(self._locks) - self._max_entries
        if excess <= 0:
            return
        for key in sorted(self._data, key=lambda k: self._data[k][0]):
            if excess <= 0:
                break
            lock = self._locks.get(key)
            if lock is not None and not lock.locked():
                self._data.pop(key, None)
                self._locks.pop(key, None)
                excess -= 1

    def get(self, key: str, ttl: float, factory: Callable[[], Any]) -> Any:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
            self._evict()
        with lock:
            entry = self._data.get(key)
            if entry and self._clock() - entry[0] < ttl:
                return entry[1]
            value = factory()
            self._data[key] = (self._clock(), value)
            return value

    def peek(self, key: str) -> Any | None:
        entry = self._data.get(key)
        return entry[1] if entry else None

    def age(self, key: str) -> float | None:
        entry = self._data.get(key)
        return (self._clock() - entry[0]) if entry else None

    def invalidate(self, key: str) -> None:
        with self._guard:
            self._data.pop(key, None)
