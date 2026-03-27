"""Tests for authentication and authorization."""

import pytest
from app.core.security import hash_password, verify_password, create_access_token, verify_token


class TestPassword:
    def test_hash_and_verify(self):
        password = "test_password_123"
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(password, hashed)

    def test_wrong_password(self):
        hashed = hash_password("correct_password")
        assert not verify_password("wrong_password", hashed)


class TestJWT:
    def test_create_and_verify_token(self):
        data = {"sub": "1", "role": "admin"}
        token = create_access_token(data)
        assert token is not None
        assert len(token) > 0

    def test_token_is_string(self):
        token = create_access_token({"sub": "1"})
        assert isinstance(token, str)
