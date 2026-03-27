from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.deposit_address import DepositAddress
from app.core.deps import require_module
from app.core.hdwallet import generate_addresses
from app.config import settings
from app.services.chain_client import ChainClient
from app.schemas.address import (
    AddressOut,
    AddressListResponse,
    GenerateRequest,
    GenerateResponse,
    UpdateLabelRequest,
    AddressStatusResponse,
    AddressBalanceResponse,
)

router = APIRouter(prefix="/addresses", tags=["地址管理"])

CanManageAddresses = Depends(require_module("addresses"))


@router.get("", response_model=AddressListResponse)
async def list_addresses(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageAddresses],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    chain: str | None = None,
    search: str | None = None,
):
    """地址列表（分页、链筛选、搜索）"""
    query = select(DepositAddress)
    count_query = select(func.count(DepositAddress.id))

    if chain:
        query = query.where(DepositAddress.chain == chain.upper())
        count_query = count_query.where(DepositAddress.chain == chain.upper())

    if search:
        filter_cond = or_(
            DepositAddress.address.ilike(f"%{search}%"),
            DepositAddress.label.ilike(f"%{search}%"),
        )
        query = query.where(filter_cond)
        count_query = count_query.where(filter_cond)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(DepositAddress.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return AddressListResponse(
        items=[AddressOut.model_validate(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/status", response_model=AddressStatusResponse)
async def get_address_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageAddresses],
):
    """HD 钱包状态（助记词是否配置、各链地址数）"""
    mnemonic_configured = bool(
        (settings.HD_MNEMONIC and settings.HD_MNEMONIC.strip())
        or (settings.HD_MNEMONIC_ENCRYPTED and settings.HD_ENCRYPTION_KEY)
    )

    total_result = await db.execute(select(func.count(DepositAddress.id)))
    total_addresses = total_result.scalar() or 0

    bsc_result = await db.execute(
        select(func.count(DepositAddress.id)).where(DepositAddress.chain == "BSC")
    )
    bsc_count = bsc_result.scalar() or 0

    tron_result = await db.execute(
        select(func.count(DepositAddress.id)).where(DepositAddress.chain == "TRON")
    )
    tron_count = tron_result.scalar() or 0

    return AddressStatusResponse(
        mnemonic_configured=mnemonic_configured,
        total_addresses=total_addresses,
        bsc_count=bsc_count,
        tron_count=tron_count,
    )


@router.post("/generate", response_model=GenerateResponse)
async def generate_new_addresses(
    body: GenerateRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageAddresses],
):
    """批量生成新地址"""
    # 查询该链当前最大 derive_index
    result = await db.execute(
        select(func.max(DepositAddress.derive_index))
        .where(DepositAddress.chain == body.chain)
    )
    max_index = result.scalar()
    start_index = (max_index + 1) if max_index is not None else 0

    # 派生地址
    derived = generate_addresses(body.chain, start_index, body.count)

    # 批量插入
    new_records = []
    for idx, address in derived:
        record = DepositAddress(
            chain=body.chain,
            derive_index=idx,
            address=address,
            label=body.label,
        )
        db.add(record)
        new_records.append(record)

    await db.flush()

    # 审计日志
    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="generate_addresses",
        detail=f"生成 {body.count} 个 {body.chain} 地址 (index {start_index}-{start_index + body.count - 1})",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return GenerateResponse(
        generated=len(new_records),
        addresses=[AddressOut.model_validate(r) for r in new_records],
    )


@router.get("/{address_id}/balance", response_model=AddressBalanceResponse)
async def get_address_balance(
    address_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageAddresses],
):
    """查询地址的原生代币 + USDT 余额"""
    result = await db.execute(
        select(DepositAddress).where(DepositAddress.id == address_id)
    )
    addr = result.scalar_one_or_none()
    if not addr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="地址不存在")

    client = ChainClient()
    native_symbol = "BNB" if addr.chain == "BSC" else "TRX"
    try:
        native_balance = await client.get_native_balance(addr.chain, addr.address)
    except Exception:
        native_balance = None
    try:
        usdt_balance = await client.get_usdt_balance(addr.chain, addr.address)
    except Exception:
        usdt_balance = None

    return AddressBalanceResponse(
        native_symbol=native_symbol,
        native_balance=str(native_balance) if native_balance is not None else None,
        usdt_balance=str(usdt_balance) if usdt_balance is not None else None,
    )


@router.get("/{address_id}", response_model=AddressOut)
async def get_address(
    address_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageAddresses],
):
    """获取单个地址详情"""
    result = await db.execute(
        select(DepositAddress).where(DepositAddress.id == address_id)
    )
    addr = result.scalar_one_or_none()
    if not addr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="地址不存在")
    return AddressOut.model_validate(addr)


@router.put("/{address_id}", response_model=AddressOut)
async def update_address_label(
    address_id: int,
    body: UpdateLabelRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageAddresses],
):
    """更新地址备注"""
    result = await db.execute(
        select(DepositAddress).where(DepositAddress.id == address_id)
    )
    addr = result.scalar_one_or_none()
    if not addr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="地址不存在")

    addr.label = body.label
    db.add(addr)

    log = AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_address_label",
        detail=f"更新地址备注: {addr.address} → {body.label}",
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)

    return AddressOut.model_validate(addr)
