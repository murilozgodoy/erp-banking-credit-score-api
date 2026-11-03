"""Limite de mensagens ao assistente por `sub`: janela deslizante em memória (por instância)."""
from __future__ import annotations

import math
import threading
import time
from collections import deque

MAX_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(self, limit: int = 10, window_seconds: float = 60.0, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> tuple[bool, int]:
        """Registra uma mensagem se couber na janela. Devolve `(permitido, segundos até liberar)`."""
        now = self.clock()
        with self._lock:
            if len(self._hits) > MAX_KEYS:  # descarta quem já saiu da janela para não crescer sem limite
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - self.window}
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = SlidingWindowLimiter()
