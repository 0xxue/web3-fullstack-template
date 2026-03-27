"""Tests for login rate limiter."""

import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException
from app.middleware.rate_limit import LoginRateLimiter


class TestLoginRateLimiter:
    def test_allows_normal_requests(self):
        limiter = LoginRateLimiter(max_attempts=5, window=60)
        request = MagicMock()
        request.client.host = "1.2.3.4"

        for _ in range(5):
            limiter.check(request)  # Should not raise

    def test_blocks_after_limit(self):
        limiter = LoginRateLimiter(max_attempts=3, window=60)
        request = MagicMock()
        request.client.host = "1.2.3.4"

        for _ in range(3):
            limiter.check(request)

        with pytest.raises(HTTPException) as exc_info:
            limiter.check(request)
        assert exc_info.value.status_code == 429

    def test_different_ips_independent(self):
        limiter = LoginRateLimiter(max_attempts=2, window=60)
        req1 = MagicMock()
        req1.client.host = "1.1.1.1"
        req2 = MagicMock()
        req2.client.host = "2.2.2.2"

        for _ in range(2):
            limiter.check(req1)
            limiter.check(req2)

        # req1 is blocked
        with pytest.raises(HTTPException):
            limiter.check(req1)

        # req2 is also blocked (independently)
        with pytest.raises(HTTPException):
            limiter.check(req2)
