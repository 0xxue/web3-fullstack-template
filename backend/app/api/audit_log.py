from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.core.deps import require_module
from app.schemas.audit_log import AuditLogOut, AuditLogListResponse

router = APIRouter(prefix="/audit-logs", tags=["审计日志"])

CanViewAuditLogs = Depends(require_module("audit_logs"))


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanViewAuditLogs],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
):
    """获取审计日志列表"""
    query = select(AuditLog)
    count_query = select(func.count(AuditLog.id))

    if search:
        filter_cond = or_(
            AuditLog.admin_username.ilike(f"%{search}%"),
            AuditLog.action.ilike(f"%{search}%"),
            AuditLog.detail.ilike(f"%{search}%"),
        )
        query = query.where(filter_cond)
        count_query = count_query.where(filter_cond)

    count_result = await db.execute(count_query)
    total = count_result.scalar()

    result = await db.execute(
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()

    return AuditLogListResponse(
        items=[AuditLogOut.model_validate(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
    )
