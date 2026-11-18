"""Fixed-window rate limiter backed by Redis, with an in-process fallback when Redis is unavailable."""
from __future__ import annotations

import logging
import threading
import time

import redis

from app.core.config import get_settings
from app.core.errors import RateLimited

log = logging.getLogger("above.ratelimit")


class RateLimiter:
    def __init__(self) -> None:
        self._redis: redis.Redis | None = None
        self._local: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()
        try:
            client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
            client.ping()
            self._redis = client
        except Exception:  # pragma: no cover - environment dependent
            log.warning("Redis unavailable; using in-process rate limiting")

    def hit(self, bucket: str, limit: int, window_seconds: int = 60) -> None:
        window = int(time.time() // window_seconds)
        key = f"rl:{bucket}:{window}"
        count: int
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, window_seconds + 1)
                count = int(pipe.execute()[0])
            except Exception:
                count = self._local_hit(key, window)
        else:
            count = self._local_hit(key, window)
        if count > limit:
            raise RateLimited("Too many requests, please slow down", details={"retry_after_seconds": window_seconds})

    def _local_hit(self, key: str, window: int) -> int:
        with self._lock:
            w, c = self._local.get(key, (window, 0))
            c = c + 1 if w == window else 1
            self._local[key] = (window, c)
            if len(self._local) > 10000:
                self._local = {k: v for k, v in self._local.items() if v[0] == window}
            return c

    def reset(self) -> None:
        with self._lock:
            self._local.clear()
        if self._redis is not None:
            try:
                for k in self._redis.scan_iter("rl:*"):
                    self._redis.delete(k)
            except Exception:
                pass


limiter = RateLimiter()
