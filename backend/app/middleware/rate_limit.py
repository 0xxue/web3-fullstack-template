"""Simple in-memory rate limiter for login endpoint."""

import time
from collections import defaultdict
from fastapi import Request, HTTPException


class LoginRateLimiter:
    """Limit login attempts per IP: max 10 per minute."""

    def __init__(self, max_attempts: int = 10, window: int = 60):
        self.max_attempts = max_attempts
        self.window = window
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def check(self, request: Request):
        ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Clean old entries
        self._attempts[ip] = [t for t in self._attempts[ip] if t > now - self.window]

        if len(self._attempts[ip]) >= self.max_attempts:
            raise HTTPException(
                status_code=429,
                detail=f"Too many login attempts. Try again in {self.window} seconds.",
            )

        self._attempts[ip].append(now)


login_limiter = LoginRateLimiter()
