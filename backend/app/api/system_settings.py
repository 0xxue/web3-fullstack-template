import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.system_settings import SystemSettings
from app.models.audit_log import AuditLog
from app.core.deps import require_role, require_module
from app.schemas.system_settings import (
    SystemSettingsOut, SystemSettingsUpdate, SystemSettingsPublic,
    TelegramConfigOut, TelegramConfigUpdate, TelegramTestRequest,
    RolePermissionsOut, RolePermissionsUpdate, ModuleInfo,
    ApiConfigOut, ApiConfigUpdate,
    NotificationTemplatesOut, NotificationTemplatesUpdate,
    NotificationTypeInfo, NotificationVariableInfo,
)
from app.schemas.auth import MessageResponse
from app.core.permissions import ALL_MODULES, MODULE_LABELS, DEFAULT_PERMISSIONS
from app.core.notification_defaults import (
    DEFAULT_NOTIFICATION_TEMPLATES,
    NOTIFICATION_TYPE_META,
    NOTIFICATION_TYPES,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["系统设置"])

SuperAdmin = Depends(require_role("super_admin"))
CanManageParams = Depends(require_module("system_params"))
CanManageTelegram = Depends(require_module("telegram_config"))
CanManageApiConfig = Depends(require_module("api_config"))


async def get_or_create_settings(db: AsyncSession) -> SystemSettings:
    """获取或创建默认系统设置（单例）"""
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = SystemSettings(id=1)
        db.add(settings)
        await db.flush()
        await db.refresh(settings)
    return settings


@router.get("/public", response_model=SystemSettingsPublic)
async def get_public_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """获取公开设置（无需登录，登录页使用）"""
    settings = await get_or_create_settings(db)
    return SystemSettingsPublic(
        require_2fa=settings.require_2fa,
        enable_google_login=settings.enable_google_login,
        google_client_id=settings.google_client_id,
    )


@router.get("", response_model=SystemSettingsOut)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageParams],
):
    """获取完整系统设置"""
    settings = await get_or_create_settings(db)
    return SystemSettingsOut.model_validate(settings)


@router.put("", response_model=SystemSettingsOut)
async def update_settings(
    body: SystemSettingsUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageParams],
):
    """更新系统设置"""
    settings = await get_or_create_settings(db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)

    db.add(settings)
    await db.flush()
    await db.refresh(settings)

    # 审计日志
    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_system_settings",
        detail=f"更新系统设置: {list(update_data.keys())}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return SystemSettingsOut.model_validate(settings)


# ─── Telegram 配置 ────────────────────────────────────


@router.get("/telegram", response_model=TelegramConfigOut)
async def get_telegram_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageTelegram],
):
    """获取 Telegram 配置"""
    settings = await get_or_create_settings(db)
    return TelegramConfigOut.model_validate(settings)


@router.put("/telegram", response_model=TelegramConfigOut)
async def update_telegram_config(
    body: TelegramConfigUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageTelegram],
):
    """更新 Telegram 配置（仅超管）"""
    settings = await get_or_create_settings(db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)

    db.add(settings)
    await db.flush()
    await db.refresh(settings)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_telegram_config",
        detail=f"更新 Telegram 配置: {list(update_data.keys())}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return TelegramConfigOut.model_validate(settings)


@router.post("/telegram/test", response_model=MessageResponse)
async def test_telegram(
    body: TelegramTestRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageTelegram],
):
    """发送 Telegram 测试消息"""
    settings = await get_or_create_settings(db)

    if not settings.tg_bot_token:
        raise HTTPException(status_code=400, detail="请先配置 Bot Token")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
                json={
                    "chat_id": body.chat_id,
                    "text": "🔔 多签钱包系统测试消息\n\n如果您收到此消息，说明 Telegram Bot 配置正确。",
                    "parse_mode": "HTML",
                },
            )
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(
                    status_code=400,
                    detail=f"发送失败: {data.get('description', '未知错误')}",
                )
    except httpx.TimeoutException:
        raise HTTPException(status_code=400, detail="发送超时，请检查网络连接")
    except httpx.RequestError as e:
        raise HTTPException(status_code=400, detail=f"请求失败: {str(e)}")

    return MessageResponse(message="测试消息发送成功")


@router.delete("/telegram/group", response_model=MessageResponse)
async def unbind_telegram_group(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageTelegram],
):
    """解除 Telegram 群组绑定"""
    settings = await get_or_create_settings(db)

    if not settings.tg_admin_chat_id:
        raise HTTPException(status_code=400, detail="当前没有绑定群组")

    old_chat_id = settings.tg_admin_chat_id
    settings.tg_admin_chat_id = None
    db.add(settings)
    await db.flush()

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="unbind_telegram_group",
        detail=f"解除 Telegram 群组绑定 (原 chat_id: {old_chat_id})",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message="Telegram 群组绑定已解除")


# ─── 角色权限配置 ──────────────────────────────────


def _build_permissions_response(settings: SystemSettings) -> RolePermissionsOut:
    current_perms = settings.role_permissions or {}
    return RolePermissionsOut(
        all_modules=[ModuleInfo(key=m, label=MODULE_LABELS[m]) for m in ALL_MODULES],
        defaults=DEFAULT_PERMISSIONS,
        current={
            role: current_perms.get(role, DEFAULT_PERMISSIONS.get(role, []))
            for role in ("operator", "signer", "viewer")
        },
    )


@router.get("/permissions", response_model=RolePermissionsOut)
async def get_role_permissions(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """获取角色权限配置（仅超管）"""
    settings = await get_or_create_settings(db)
    return _build_permissions_response(settings)


@router.put("/permissions", response_model=RolePermissionsOut)
async def update_role_permissions(
    body: RolePermissionsUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """更新角色权限配置（仅超管）"""
    # 校验模块名
    for role_modules in [body.operator, body.signer, body.viewer]:
        for m in role_modules:
            if m not in ALL_MODULES:
                raise HTTPException(status_code=400, detail=f"无效模块: {m}")

    settings = await get_or_create_settings(db)
    settings.role_permissions = {
        "operator": body.operator,
        "signer": body.signer,
        "viewer": body.viewer,
    }
    db.add(settings)
    await db.flush()
    await db.refresh(settings)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_role_permissions",
        detail="更新角色权限配置",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return _build_permissions_response(settings)


# ─── API / RPC 配置 ───────────────────────────────


@router.get("/api-config", response_model=ApiConfigOut)
async def get_api_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageApiConfig],
):
    """获取 API / RPC 配置"""
    settings = await get_or_create_settings(db)
    return ApiConfigOut.model_validate(settings)


@router.put("/api-config", response_model=ApiConfigOut)
async def update_api_config(
    body: ApiConfigUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageApiConfig],
):
    """更新 API / RPC 配置"""
    settings = await get_or_create_settings(db)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)

    db.add(settings)
    await db.flush()
    await db.refresh(settings)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_api_config",
        detail=f"更新 API 配置: {list(update_data.keys())}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return ApiConfigOut.model_validate(settings)


# ─── 通知模板配置 ──────────────────────────────────


def _build_notification_templates_response(settings: SystemSettings) -> NotificationTemplatesOut:
    """合并默认模板与自定义配置，返回完整列表。"""
    custom_all = settings.notification_templates or {}
    types = []
    for key in NOTIFICATION_TYPES:
        defaults = DEFAULT_NOTIFICATION_TEMPLATES[key]
        meta = NOTIFICATION_TYPE_META[key]
        custom = custom_all.get(key, {}) if isinstance(custom_all, dict) else {}

        info = NotificationTypeInfo(
            key=key,
            label=meta["label"],
            variables=[NotificationVariableInfo(**v) for v in meta["variables"]],
            enabled=custom.get("enabled", defaults["enabled"]),
            template=custom.get("template", defaults["template"]),
            group=custom.get("group", defaults["group"]),
            dm=custom.get("dm", defaults["dm"]),
        )
        # 大额充值附带阈值
        if key == "large_deposit":
            val = settings.large_deposit_threshold or 10000
            info.threshold = str(int(val)) if val == int(val) else str(val)
        types.append(info)
    return NotificationTemplatesOut(types=types)


@router.get("/telegram/notification-templates", response_model=NotificationTemplatesOut)
async def get_notification_templates(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageTelegram],
):
    """获取通知模板配置（7 种通知 + 可用变量）"""
    settings = await get_or_create_settings(db)
    return _build_notification_templates_response(settings)


@router.put("/telegram/notification-templates", response_model=NotificationTemplatesOut)
async def update_notification_templates(
    body: NotificationTemplatesUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageTelegram],
):
    """更新通知模板配置"""
    # 校验 key
    for key in body.templates:
        if key not in NOTIFICATION_TYPES:
            raise HTTPException(status_code=400, detail=f"无效通知类型: {key}")

    # 校验模板语法
    for key, tmpl_update in body.templates.items():
        if tmpl_update.template is not None:
            try:
                test_vars = {v["name"]: "test" for v in NOTIFICATION_TYPE_META[key]["variables"]}
                tmpl_update.template.format_map(test_vars)
            except (KeyError, ValueError, IndexError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"模板语法错误 ({key}): {e}",
                )

    import copy
    from decimal import Decimal

    settings = await get_or_create_settings(db)
    # deepcopy 确保 SQLAlchemy 检测到 JSON 列变更
    current = copy.deepcopy(settings.notification_templates) if settings.notification_templates else {}
    if not isinstance(current, dict):
        current = {}

    for key, tmpl_update in body.templates.items():
        entry = current.get(key, {})
        update_data = tmpl_update.model_dump(exclude_unset=True)
        # threshold 单独处理，存到 system_settings.large_deposit_threshold
        threshold_val = update_data.pop("threshold", None)
        if key == "large_deposit" and threshold_val is not None:
            settings.large_deposit_threshold = Decimal(threshold_val)
        entry.update(update_data)
        current[key] = entry

    settings.notification_templates = current
    db.add(settings)
    await db.flush()
    await db.refresh(settings)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_notification_templates",
        detail=f"更新通知模板: {list(body.templates.keys())}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return _build_notification_templates_response(settings)


@router.post("/telegram/notification-templates/reset", response_model=NotificationTemplatesOut)
async def reset_notification_templates(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageTelegram],
):
    """重置所有通知模板为默认值"""
    from decimal import Decimal

    settings = await get_or_create_settings(db)
    settings.notification_templates = None
    settings.large_deposit_threshold = Decimal("10000")
    db.add(settings)
    await db.flush()
    await db.refresh(settings)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="reset_notification_templates",
        detail="重置所有通知模板为默认值",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return _build_notification_templates_response(settings)
