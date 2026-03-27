from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.system_settings import SystemSettings
from app.core.security import (
    verify_password,
    hash_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_totp_secret,
    get_totp_uri,
    verify_totp,
)
from app.core.deps import get_current_user
from app.core.permissions import resolve_permissions
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    TwoFALoginRequired,
    UserInfo,
    RefreshRequest,
    RefreshResponse,
    ChangePasswordRequest,
    Setup2FAResponse,
    Verify2FARequest,
    GoogleLoginRequest,
    BindGoogleEmailRequest,
    UnbindGoogleEmailRequest,
    MessageResponse,
)

router = APIRouter(prefix="/auth", tags=["认证"])


async def _get_session_timeout(db: AsyncSession) -> int | None:
    """从系统设置读取会话超时分钟数，返回 None 则使用默认值"""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    settings = result.scalar_one_or_none()
    if settings and settings.session_timeout_minutes:
        return settings.session_timeout_minutes
    return None


async def build_user_info(user: Admin, db: AsyncSession) -> UserInfo:
    """构建 UserInfo（含权限列表）"""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    settings = result.scalar_one_or_none()
    role_perms = settings.role_permissions if settings else None
    permissions = resolve_permissions(user.role, role_perms)
    return UserInfo(
        id=user.id,
        username=user.username,
        role=user.role,
        avatar=user.username[0].upper(),
        totp_enabled=user.totp_enabled,
        google_email=user.google_email,
        permissions=permissions,
    )


@router.post("/login", response_model=LoginResponse | TwoFALoginRequired)
async def login(
    body: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """用户登录"""
    result = await db.execute(select(Admin).where(Admin.username == body.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用，请联系超级管理员",
        )

    # 如果启用了 2FA
    if user.totp_enabled:
        if not body.totp_code:
            # 返回需要 2FA 的提示
            temp_token = create_access_token({"sub": str(user.id), "2fa_pending": True})
            return TwoFALoginRequired(temp_token=temp_token)

        # 验证 2FA 码
        if not verify_totp(user.totp_secret, body.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="两步验证码错误",
            )

    # 生成 tokens
    timeout = await _get_session_timeout(db)
    token_data = {"sub": str(user.id), "ver": user.token_version}
    access_token = create_access_token(token_data, expire_minutes=timeout)
    refresh_token = create_refresh_token(token_data)

    # 记录登录日志
    log = AuditLog(
        admin_id=user.id,
        admin_username=user.username,
        action="login",
        detail="用户登录成功",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=await build_user_info(user, db),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """刷新 Access Token"""
    payload = decode_token(body.refresh_token)

    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的刷新令牌",
        )

    admin_id = payload.get("sub")
    result = await db.execute(select(Admin).where(Admin.id == int(admin_id)))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    # 校验 token 版本（被强制下线则 refresh 也失败）
    token_ver = payload.get("ver", 0)
    if token_ver != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="会话已失效，请重新登录",
        )

    timeout = await _get_session_timeout(db)
    new_access_token = create_access_token(
        {"sub": str(user.id), "ver": user.token_version},
        expire_minutes=timeout,
    )
    return RefreshResponse(access_token=new_access_token)


@router.get("/me", response_model=UserInfo)
async def get_me(
    current_user: Annotated[Admin, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """获取当前用户信息"""
    return await build_user_info(current_user, db)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: Annotated[Admin, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """修改自己的密码"""
    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="原密码错误",
        )

    if body.old_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能与原密码相同",
        )

    current_user.password_hash = hash_password(body.new_password)
    db.add(current_user)

    # 审计日志
    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="change_password",
        detail="用户修改密码",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message="密码修改成功")


# ─── 2FA ────────────────────────────────────────────────

@router.post("/2fa/setup", response_model=Setup2FAResponse)
async def setup_2fa(
    current_user: Annotated[Admin, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """生成 2FA 密钥（未启用时调用）"""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="两步验证已启用",
        )

    secret = generate_totp_secret()
    current_user.totp_secret = secret
    db.add(current_user)

    qr_uri = get_totp_uri(secret, current_user.username)

    return Setup2FAResponse(secret=secret, qr_uri=qr_uri)


@router.post("/2fa/enable", response_model=MessageResponse)
async def enable_2fa(
    body: Verify2FARequest,
    request: Request,
    current_user: Annotated[Admin, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """验证并启用 2FA"""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="两步验证已启用",
        )

    if not current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先调用 /2fa/setup 生成密钥",
        )

    if not verify_totp(current_user.totp_secret, body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码错误，请重试",
        )

    current_user.totp_enabled = True
    db.add(current_user)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="enable_2fa",
        detail="用户启用两步验证",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message="两步验证已启用")


@router.post("/2fa/disable", response_model=MessageResponse)
async def disable_2fa(
    body: Verify2FARequest,
    request: Request,
    current_user: Annotated[Admin, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """关闭 2FA（需要验证码确认）"""
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="两步验证未启用",
        )

    if not verify_totp(current_user.totp_secret, body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码错误",
        )

    current_user.totp_enabled = False
    current_user.totp_secret = None
    db.add(current_user)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="disable_2fa",
        detail="用户关闭两步验证",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message="两步验证已关闭")


# ─── Google Email Binding ──────────────────────────────

@router.post("/google-email/bind", response_model=MessageResponse)
async def bind_google_email(
    body: BindGoogleEmailRequest,
    request: Request,
    current_user: Annotated[Admin, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """绑定自己的 Google 邮箱"""
    if current_user.google_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="已绑定 Google 邮箱，请先解绑再重新绑定",
        )

    import re
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', body.google_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱格式不正确",
        )

    result = await db.execute(
        select(Admin).where(Admin.google_email == body.google_email)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该邮箱已被其他账户绑定",
        )

    current_user.google_email = body.google_email
    db.add(current_user)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="bind_google_email",
        detail=f"绑定 Google 邮箱: {body.google_email}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message="Google 邮箱绑定成功")


@router.post("/google-email/unbind", response_model=MessageResponse)
async def unbind_google_email(
    body: UnbindGoogleEmailRequest,
    request: Request,
    current_user: Annotated[Admin, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """解绑自己的 Google 邮箱（需要 2FA 验证码）"""
    if not current_user.google_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未绑定 Google 邮箱",
        )

    if not current_user.totp_enabled or not current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先启用两步验证后再操作",
        )

    if not verify_totp(current_user.totp_secret, body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码错误",
        )

    old_email = current_user.google_email
    current_user.google_email = None
    db.add(current_user)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="unbind_google_email",
        detail=f"解绑 Google 邮箱: {old_email}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message="Google 邮箱已解绑")


# ─── Google OAuth ───────────────────────────────────────

@router.post("/google", response_model=LoginResponse)
async def google_login(
    body: GoogleLoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Google 一键登录"""
    # 检查是否启用了 Google 登录
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    sys_settings = result.scalar_one_or_none()

    if sys_settings is None or not sys_settings.enable_google_login:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google 登录未启用",
        )

    if not sys_settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Client ID 未配置",
        )

    # 验证 Google ID Token
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={body.credential}"
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google 令牌无效",
        )

    google_data = resp.json()

    # 验证 audience 匹配
    if google_data.get("aud") != sys_settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google Client ID 不匹配",
        )

    google_email = google_data.get("email")
    if not google_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无法获取 Google 邮箱",
        )

    # 查找绑定了此邮箱的管理员
    result = await db.execute(select(Admin).where(Admin.google_email == google_email))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"邮箱 {google_email} 未绑定任何管理员账号",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用",
        )

    # 生成 tokens
    timeout = await _get_session_timeout(db)
    token_data = {"sub": str(user.id), "ver": user.token_version}
    access_token = create_access_token(token_data, expire_minutes=timeout)
    refresh_token = create_refresh_token(token_data)

    # 记录登录日志
    log = AuditLog(
        admin_id=user.id,
        admin_username=user.username,
        action="google_login",
        detail=f"Google 登录: {google_email}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=await build_user_info(user, db),
    )
