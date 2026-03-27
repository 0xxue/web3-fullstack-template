from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.core.security import hash_password
from app.core.deps import require_module
from app.schemas.admin import (
    AdminCreate,
    AdminUpdate,
    AdminOut,
    AdminListResponse,
    ResetPasswordRequest,
)
from app.schemas.auth import MessageResponse

router = APIRouter(prefix="/admins", tags=["管理员管理"])

SuperAdmin = Depends(require_module("admin_manage"))


@router.get("", response_model=AdminListResponse)
async def list_admins(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取管理员列表"""
    # 总数
    count_result = await db.execute(select(func.count(Admin.id)))
    total = count_result.scalar()

    # 分页查询
    result = await db.execute(
        select(Admin)
        .order_by(Admin.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    admins = result.scalars().all()

    return AdminListResponse(
        items=[AdminOut.model_validate(a) for a in admins],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=AdminOut, status_code=status.HTTP_201_CREATED)
async def create_admin(
    body: AdminCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """创建管理员"""
    # 检查用户名是否已存在
    existing = await db.execute(select(Admin).where(Admin.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    admin = Admin(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        signer_address_bsc=body.signer_address_bsc,
        signer_address_tron=body.signer_address_tron,
        tg_username=body.tg_username,
        tg_chat_id=body.tg_chat_id,
        google_email=body.google_email,
    )
    db.add(admin)
    await db.flush()
    await db.refresh(admin)

    # 审计日志
    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="create_admin",
        detail=f"创建管理员: {body.username} (角色: {body.role})",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return AdminOut.model_validate(admin)


@router.put("/{admin_id}", response_model=AdminOut)
async def update_admin(
    admin_id: int,
    body: AdminUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """更新管理员信息"""
    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()

    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="管理员不存在",
        )

    # 不能修改自己的角色
    if admin.id == current_user.id and body.role and body.role != current_user.role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能修改自己的角色",
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(admin, field, value)

    db.add(admin)
    await db.flush()
    await db.refresh(admin)

    # 审计日志
    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_admin",
        detail=f"更新管理员: {admin.username} | 字段: {list(update_data.keys())}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return AdminOut.model_validate(admin)


@router.delete("/{admin_id}", response_model=MessageResponse)
async def delete_admin(
    admin_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """禁用管理员（软删除）"""
    if admin_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能禁用自己的账号",
        )

    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()

    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="管理员不存在",
        )

    admin.is_active = False
    admin.token_version = (admin.token_version or 0) + 1
    db.add(admin)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="disable_admin",
        detail=f"禁用管理员: {admin.username}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message=f"管理员 {admin.username} 已禁用")


@router.post("/{admin_id}/reset-password", response_model=MessageResponse)
async def reset_password(
    admin_id: int,
    body: ResetPasswordRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """超级管理员重置其他用户密码"""
    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()

    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="管理员不存在",
        )

    admin.password_hash = hash_password(body.new_password)
    db.add(admin)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="reset_password",
        detail=f"重置管理员密码: {admin.username}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message=f"管理员 {admin.username} 密码已重置")


@router.post("/{admin_id}/kick", response_model=MessageResponse)
async def kick_admin(
    admin_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """强制下线管理员（使其所有 token 失效）"""
    if admin_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能强制下线自己",
        )

    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()

    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="管理员不存在",
        )

    admin.token_version = (admin.token_version or 0) + 1
    db.add(admin)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="kick_admin",
        detail=f"强制下线管理员: {admin.username}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message=f"管理员 {admin.username} 已被强制下线")


@router.delete("/{admin_id}/tg-binding", response_model=MessageResponse)
async def unbind_admin_tg(
    admin_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, SuperAdmin],
):
    """解除管理员的 Telegram 私聊绑定"""
    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()

    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="管理员不存在",
        )

    old_chat_id = admin.tg_chat_id
    admin.tg_chat_id = None
    db.add(admin)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="unbind_admin_tg",
        detail=f"解除管理员 {admin.username} 的 TG 绑定 (原 chat_id: {old_chat_id})",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return MessageResponse(message=f"管理员 {admin.username} 的 Telegram 绑定已解除")
