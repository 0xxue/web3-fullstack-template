from typing import Annotated
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.deposit import Deposit
from app.core.deps import require_module
from app.schemas.deposit import DepositOut, DepositListResponse, DepositStatsResponse, TokenAmount

router = APIRouter(prefix="/deposits", tags=["充值记录"])

CanViewDeposits = Depends(require_module("deposits"))


@router.get("", response_model=DepositListResponse)
async def list_deposits(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanViewDeposits],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    chain: str | None = None,
    status: str | None = None,
    search: str | None = None,
):
    """充值列表（分页 + 筛选 + 搜索）"""
    query = select(Deposit)
    count_query = select(func.count(Deposit.id))

    if chain:
        query = query.where(Deposit.chain == chain.upper())
        count_query = count_query.where(Deposit.chain == chain.upper())

    if status:
        query = query.where(Deposit.status == status)
        count_query = count_query.where(Deposit.status == status)

    if search:
        cond = or_(
            Deposit.address.ilike(f"%{search}%"),
            Deposit.tx_hash.ilike(f"%{search}%"),
            Deposit.from_address.ilike(f"%{search}%"),
        )
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Deposit.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return DepositListResponse(
        items=[DepositOut.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=DepositStatsResponse)
async def get_deposit_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanViewDeposits],
):
    """充值统计摘要"""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # 今日笔数 + 金额
    row = (await db.execute(
        select(func.count(Deposit.id), func.coalesce(func.sum(Deposit.amount), 0))
        .where(Deposit.created_at >= today_start)
    )).one()
    total_today, amount_today = row

    # 待确认
    pending_count = (await db.execute(
        select(func.count(Deposit.id)).where(Deposit.status == "pending")
    )).scalar() or 0

    # 确认中
    confirming_count = (await db.execute(
        select(func.count(Deposit.id)).where(Deposit.status == "confirming")
    )).scalar() or 0

    # 今日已确认
    confirmed_today = (await db.execute(
        select(func.count(Deposit.id)).where(
            Deposit.status == "confirmed",
            Deposit.confirmed_at >= today_start,
        )
    )).scalar() or 0

    # 按 token 分组统计今日金额
    token_rows = (await db.execute(
        select(Deposit.token, func.coalesce(func.sum(Deposit.amount), 0))
        .where(Deposit.created_at >= today_start)
        .group_by(Deposit.token)
        .order_by(Deposit.token)
    )).all()
    amount_by_token = [TokenAmount(token=t, amount=a) for t, a in token_rows]

    return DepositStatsResponse(
        total_today=total_today,
        amount_today=amount_today,
        amount_by_token=amount_by_token,
        pending_count=pending_count,
        confirming_count=confirming_count,
        confirmed_today=confirmed_today,
    )


@router.get("/{deposit_id}", response_model=DepositOut)
async def get_deposit(
    deposit_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanViewDeposits],
):
    """获取单条充值详情"""
    result = await db.execute(
        select(Deposit).where(Deposit.id == deposit_id)
    )
    deposit = result.scalar_one_or_none()
    if not deposit:
        raise HTTPException(status_code=404, detail="充值记录不存在")
    return DepositOut.model_validate(deposit)
