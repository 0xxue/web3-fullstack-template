from typing import Annotated, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.notification import Notification
from app.models.system_settings import SystemSettings
from app.core.deps import get_current_user
from app.core.permissions import resolve_permissions

router = APIRouter(prefix="/notifications", tags=["通知"])

# notif_* 权限前缀 → Notification.type 映射
NOTIF_PERM_TO_TYPE: dict[str, str] = {
    "notif_deposit": "deposit",
    "notif_large_deposit": "large_deposit",
    "notif_collection_completed": "collection_completed",
    "notif_proposal_created": "proposal_created",
    "notif_proposal_signed": "proposal_signed",
    "notif_proposal_executed": "proposal_executed",
    "notif_proposal_cancelled": "proposal_cancelled",
    "notif_payout_batch_created": "payout_batch_created",
    "notif_payout_completed": "payout_completed",
    "notif_system_alert": "system_alert",
}


async def get_allowed_types(
    current_user: Admin,
    db: AsyncSession,
) -> list[str]:
    """返回当前用户有权限查看的通知类型列表。super_admin 返回全部。"""
    if current_user.role == "super_admin":
        return list(NOTIF_PERM_TO_TYPE.values())

    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    settings = result.scalar_one_or_none()
    role_perms_json = settings.role_permissions if settings else None
    user_modules = resolve_permissions(current_user.role, role_perms_json)

    return [
        NOTIF_PERM_TO_TYPE[perm]
        for perm in user_modules
        if perm in NOTIF_PERM_TO_TYPE
    ]


class NotificationOut(BaseModel):
    id: int
    type: str
    chain: Optional[str]
    title: str
    body: Optional[str]
    extra_data: Optional[dict]
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    items: list[NotificationOut]
    total: int
    page: int
    page_size: int
    unread_count: int


class UnreadCountResponse(BaseModel):
    count: int


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
):
    """获取通知列表（分页），仅返回用户有权限的通知类型"""
    allowed_types = await get_allowed_types(current_user, db)

    if not allowed_types:
        return NotificationListResponse(
            items=[], total=0, page=page, page_size=page_size, unread_count=0
        )

    query = select(Notification).where(Notification.type.in_(allowed_types))
    count_query = select(func.count(Notification.id)).where(Notification.type.in_(allowed_types))
    unread_base = select(func.count(Notification.id)).where(
        Notification.type.in_(allowed_types),
        Notification.is_read == False,  # noqa: E712
    )

    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712
        count_query = count_query.where(Notification.is_read == False)  # noqa: E712

    total = (await db.execute(count_query)).scalar() or 0
    unread_count = (await db.execute(unread_base)).scalar() or 0

    query = query.order_by(Notification.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return NotificationListResponse(
        items=[NotificationOut.model_validate(n) for n in items],
        total=total,
        page=page,
        page_size=page_size,
        unread_count=unread_count,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, Depends(get_current_user)],
):
    """获取未读通知数量（仅统计用户有权限的类型）"""
    allowed_types = await get_allowed_types(current_user, db)

    if not allowed_types:
        return UnreadCountResponse(count=0)

    count = (await db.execute(
        select(func.count(Notification.id)).where(
            Notification.type.in_(allowed_types),
            Notification.is_read == False,  # noqa: E712
        )
    )).scalar() or 0
    return UnreadCountResponse(count=count)


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, Depends(get_current_user)],
):
    """标记单条通知为已读"""
    allowed_types = await get_allowed_types(current_user, db)

    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")
    if notification.type not in allowed_types:
        raise HTTPException(status_code=403, detail="无权限操作此通知")

    notification.is_read = True
    await db.commit()
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, Depends(get_current_user)],
):
    """标记所有（有权限的）通知为已读"""
    allowed_types = await get_allowed_types(current_user, db)

    if not allowed_types:
        return {"ok": True}

    await db.execute(
        update(Notification)
        .where(Notification.type.in_(allowed_types), Notification.is_read == False)  # noqa: E712
        .values(is_read=True)
    )
    await db.commit()
    return {"ok": True}
