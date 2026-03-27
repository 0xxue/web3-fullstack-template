from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.security import decode_token
from app.models.admin import Admin
from app.models.system_settings import SystemSettings
from app.core.permissions import resolve_permissions

security_scheme = HTTPBearer()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Admin:
    token = credentials.credentials
    payload = decode_token(token)

    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
        )

    admin_id_str = payload.get("sub")
    if admin_id_str is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌",
        )

    result = await db.execute(select(Admin).where(Admin.id == int(admin_id_str)))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    # 校验 token 版本（强制下线机制）
    token_ver = payload.get("ver", 0)
    if token_ver != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="会话已失效，请重新登录",
        )

    return user


def require_role(*roles: str):
    """角色权限依赖: require_role("super_admin", "operator")"""
    async def role_checker(
        current_user: Annotated[Admin, Depends(get_current_user)],
    ) -> Admin:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足",
            )
        return current_user
    return role_checker


def require_module(*modules: str):
    """功能模块权限依赖: require_module("addresses", "deposits")
    用户角色需拥有至少一个指定模块的访问权限。super_admin 自动放行。"""
    async def module_checker(
        current_user: Annotated[Admin, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> Admin:
        if current_user.role == "super_admin":
            return current_user

        result = await db.execute(
            select(SystemSettings).where(SystemSettings.id == 1)
        )
        settings = result.scalar_one_or_none()
        role_perms_json = settings.role_permissions if settings else None

        user_modules = resolve_permissions(current_user.role, role_perms_json)

        if not any(m in user_modules for m in modules):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足，无法访问此功能模块",
            )
        return current_user
    return module_checker
