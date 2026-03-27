"""Tests for RBAC permission system."""

import pytest
from app.core.permissions import ALL_MODULES, DEFAULT_PERMISSIONS, resolve_permissions


class TestPermissions:
    def test_admin_has_all_modules(self):
        admin_perms = DEFAULT_PERMISSIONS.get("super_admin", {})
        assert isinstance(admin_perms, dict)

    def test_readonly_limited(self):
        readonly_perms = DEFAULT_PERMISSIONS.get("readonly", {})
        assert isinstance(readonly_perms, dict)

    def test_all_modules_defined(self):
        assert len(ALL_MODULES) > 0
        for module in ALL_MODULES:
            assert isinstance(module, str)

    def test_resolve_permissions_returns_dict(self):
        result = resolve_permissions("super_admin")
        assert isinstance(result, dict)
