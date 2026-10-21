"""In-process token-bucket rate limiter. For multi-instance deployments put the same limits in your
reverse proxy (nginx limit_req / Cloudflare) or swap this for a Redis-backed bucket."""
from __future__ import annotations

import hashlib
import os
import threading
import time


class TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate, self.burst = rate, burst
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """Returns 0 if allowed, else seconds to wait."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._state[key] = (tokens - 1, now)
                if len(self._state) > 50_000:  # crude memory cap
                    self._state.clear()
                return 0.0
            self._state[key] = (tokens, now)
            return (1 - tokens) / self.rate


_scale = float(os.environ.get("SANGAM_RATE_LIMIT_SCALE", "1"))
api_bucket = TokenBucket(rate=25 * _scale, burst=int(100 * _scale))       # per API key
public_bucket = TokenBucket(rate=2 * _scale, burst=int(30 * _scale))      # per IP on customer pages


def key_for(authorization: str | None, ip: str) -> tuple[TokenBucket, str]:
    if authorization:
        return api_bucket, hashlib.sha256(authorization.encode()).hexdigest()[:24]
    return public_bucket, ip
