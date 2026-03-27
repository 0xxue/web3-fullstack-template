import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.collection import Collection, CollectionItem
from app.models.deposit_address import DepositAddress
from app.models.proposal import Proposal
from app.models.wallet import Wallet
from app.models.system_settings import SystemSettings
from app.models.audit_log import AuditLog
from app.core.deps import require_module
from app.services.chain_client import chain_client, GAS_ESTIMATE_BSC, GAS_ESTIMATE_TRON
from app.schemas.collection import (
    ScanRequest, ScanResponse, ScannedAddressItem,
    CreateCollectionRequest, CreateCollectionResponse,
    CollectionOut, CollectionItemOut, CollectionListResponse,
    CollectionWalletOption,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/collections", tags=["归集管理"])

CanManageCollections = Depends(require_module("collections"))


# ─── 扫描 ────────────────────────────────────────────

@router.post("/scan", response_model=ScanResponse)
async def scan_addresses(
    body: ScanRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageCollections],
):
    """扫描链上充值地址余额（USDT 或原生代币）"""
    chain = body.chain
    asset_type = body.asset_type  # "usdt" | "native"

    # 获取阈值
    if body.min_amount > 0:
        threshold = body.min_amount
    else:
        settings = (await db.execute(
            select(SystemSettings).where(SystemSettings.id == 1)
        )).scalar_one_or_none()
        if chain == "BSC":
            threshold = settings.collection_min_bsc if settings else Decimal("50")
        else:
            threshold = settings.collection_min_tron if settings else Decimal("10")

    # 查询 gas 钱包的 derive_index 集合（动态排除）
    gas_idx_result = await db.execute(
        select(Wallet.derive_index).where(
            Wallet.chain == chain,
            Wallet.type == "gas",
            Wallet.derive_index.isnot(None),
        )
    )
    gas_indexes = set(gas_idx_result.scalars().all())

    # 查询所有活跃充值地址（排除 gas 钱包）
    addr_query = select(DepositAddress).where(
        DepositAddress.chain == chain,
        DepositAddress.is_active == True,  # noqa: E712
    )
    if gas_indexes:
        addr_query = addr_query.where(DepositAddress.derive_index.notin_(gas_indexes))

    result = await db.execute(addr_query)
    addresses = result.scalars().all()

    if not addresses:
        return ScanResponse(
            chain=chain, threshold=threshold,
            addresses=[], total_amount=Decimal(0), count=0,
        )

    gas_estimate = GAS_ESTIMATE_BSC if chain == "BSC" else GAS_ESTIMATE_TRON

    # 批量查询余额（BSC: Multicall3 合约, TRON: 并发30）
    addr_list = [a.address for a in addresses]
    addr_map = {a.address: a for a in addresses}

    logger.info("开始扫描 %s %d 个地址余额", chain, len(addr_list))
    balance_results = await chain_client.batch_get_balances(chain, addr_list)
    logger.info("扫描完成, 返回 %d 条结果", len(balance_results))

    scanned: list[ScannedAddressItem] = []
    total_amount = Decimal(0)

    for bal in balance_results:
        addr = addr_map.get(bal["address"])
        if not addr:
            continue
        usdt_bal = bal["usdt"]
        native_bal = bal["native"]

        if asset_type == "native":
            effective_bal = native_bal - gas_estimate
            if effective_bal < threshold:
                continue
            scanned.append(ScannedAddressItem(
                address=addr.address,
                derive_index=addr.derive_index,
                balance=effective_bal,
                native_balance=native_bal,
                gas_needed=Decimal(0),
                gas_sufficient=True,
                label=addr.label if hasattr(addr, "label") else None,
            ))
            total_amount += effective_bal
        else:
            if usdt_bal < threshold:
                continue
            scanned.append(ScannedAddressItem(
                address=addr.address,
                derive_index=addr.derive_index,
                balance=usdt_bal,
                native_balance=native_bal,
                gas_needed=gas_estimate,
                gas_sufficient=native_bal >= gas_estimate,
                label=addr.label if hasattr(addr, "label") else None,
            ))
            total_amount += usdt_bal

    # 按余额降序
    scanned.sort(key=lambda x: x.balance, reverse=True)

    return ScanResponse(
        chain=chain,
        threshold=threshold,
        addresses=scanned,
        total_amount=total_amount,
        count=len(scanned),
    )


# ─── 列出归集目标钱包 ────────────────────────────────────

@router.get("/wallets", response_model=list[CollectionWalletOption])
async def list_collection_wallets(
    chain: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageCollections],
):
    """返回指定链的所有可用归集钱包"""
    wallets = (await db.execute(
        select(Wallet).where(
            Wallet.chain == chain,
            Wallet.type == "collection",
            Wallet.is_active == True,
            Wallet.address.isnot(None),
        ).order_by(Wallet.id.desc())
    )).scalars().all()
    return [
        CollectionWalletOption(
            id=w.id,
            address=w.address,
            label=w.label,
            is_multisig=w.is_multisig or False,
            multisig_status=w.multisig_status,
        )
        for w in wallets
    ]


# ─── 创建归集 ─────────────────────────────────────────

@router.post("", response_model=CreateCollectionResponse)
async def create_collection(
    body: CreateCollectionRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageCollections],
):
    """创建归集批次 + 多签提案（等待 2/3 签名后执行）"""
    chain = body.chain

    # 校验归集钱包已配置（优先使用前端指定的 wallet_id）
    if body.wallet_id:
        wallet = (await db.execute(
            select(Wallet).where(
                Wallet.id == body.wallet_id,
                Wallet.chain == chain,
                Wallet.type == "collection",
                Wallet.is_active == True,
            )
        )).scalar_one_or_none()
        if not wallet or not wallet.address:
            raise HTTPException(status_code=400, detail=f"指定的归集钱包不存在或已停用")
    else:
        wallet = (await db.execute(
            select(Wallet).where(
                Wallet.chain == chain, Wallet.type == "collection", Wallet.is_active == True
            ).order_by(Wallet.id).limit(1)
        )).scalar_one_or_none()
        if not wallet or not wallet.address:
            raise HTTPException(status_code=400, detail=f"{chain} 归集钱包未配置")

    # 校验无正在执行/待签名的同链归集
    active = (await db.execute(
        select(func.count(Collection.id)).where(
            Collection.chain == chain,
            Collection.status.in_(["pending", "signing", "executing"]),
        )
    )).scalar() or 0
    if active > 0:
        raise HTTPException(status_code=400, detail=f"{chain} 已有进行中的归集任务")

    # 校验地址存在
    addr_set = {a.address for a in body.addresses}
    existing = (await db.execute(
        select(DepositAddress.address).where(
            DepositAddress.chain == chain,
            DepositAddress.address.in_(addr_set),
        )
    )).scalars().all()
    existing_set = set(existing)
    missing = addr_set - existing_set
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"以下地址不存在: {', '.join(list(missing)[:3])}",
        )

    # 创建 Collection（状态 pending，等待签名）
    total_amount = sum(a.amount for a in body.addresses)
    collection = Collection(
        chain=chain,
        asset_type=body.asset_type,
        status="pending",
        total_amount=total_amount,
        address_count=len(body.addresses),
        created_by=current_user.id,
    )
    db.add(collection)
    await db.flush()

    # 创建 CollectionItems
    for addr_item in body.addresses:
        item = CollectionItem(
            collection_id=collection.id,
            address=addr_item.address,
            amount=addr_item.amount,
            status="pending",
        )
        db.add(item)

    # 构建归集摘要用于签名（keccak256 / sha256）
    collection_data = {
        "collection_id": collection.id,
        "chain": chain,
        "target_address": wallet.address,
        "addresses": [{"address": a.address, "amount": str(a.amount)} for a in body.addresses],
        "total_amount": str(total_amount),
    }
    data_json = json.dumps(collection_data, sort_keys=True)
    collection_hash = "0x" + hashlib.sha256(data_json.encode()).hexdigest()

    # 创建 Proposal
    proposal = Proposal(
        chain=chain,
        type="collection",
        status="pending",
        title=f"{chain} 归集: {len(body.addresses)} 个地址, {total_amount} USDT",
        description=f"归集到 {wallet.address[:10]}..., 共 {len(body.addresses)} 个地址",
        wallet_id=wallet.id,
        tx_data=json.dumps(collection_data, default=str),
        safe_tx_hash=collection_hash,
        threshold=wallet.threshold or 2,
        current_signatures=0,
        created_by=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(proposal)
    await db.flush()

    # 关联 proposal 到 collection
    collection.proposal_id = proposal.id

    # 审计日志
    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="create_collection",
        detail=f"创建{chain}归集提案: {len(body.addresses)} 个地址, {total_amount} USDT, 提案#{proposal.id}",
        ip_address=request.client.host if request.client else None,
    ))

    await db.commit()
    await db.refresh(collection)

    # TG 通知：归集提案已创建
    from app.core.telegram import notifier
    asyncio.create_task(notifier.notify_proposal_created(
        chain=chain,
        proposal_type="collection",
        title=proposal.title or "",
        threshold=wallet.threshold or 2,
        creator_name=current_user.username,
    ))

    return CreateCollectionResponse(
        id=collection.id,
        chain=collection.chain,
        status=collection.status,
        address_count=collection.address_count,
        total_amount=collection.total_amount,
        proposal_id=proposal.id,
        created_at=collection.created_at,
    )


# ─── 列表 ────────────────────────────────────────────

@router.get("", response_model=CollectionListResponse)
async def list_collections(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageCollections],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    chain: str | None = None,
    status: str | None = None,
):
    """归集列表（分页）"""
    query = select(Collection)
    count_query = select(func.count(Collection.id))

    if chain:
        query = query.where(Collection.chain == chain.upper())
        count_query = count_query.where(Collection.chain == chain.upper())

    if status:
        query = query.where(Collection.status == status)
        count_query = count_query.where(Collection.status == status)

    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Collection.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return CollectionListResponse(
        items=[CollectionOut.model_validate(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── 详情 ────────────────────────────────────────────

@router.get("/{collection_id}", response_model=CollectionOut)
async def get_collection_detail(
    collection_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageCollections],
):
    """归集详情（含明细项）"""
    collection = (await db.execute(
        select(Collection).where(Collection.id == collection_id)
    )).scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="归集记录不存在")

    items = (await db.execute(
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection_id)
        .order_by(CollectionItem.id)
    )).scalars().all()

    out = CollectionOut.model_validate(collection)
    out.items = [CollectionItemOut.model_validate(i) for i in items]

    # 查归集目标钱包地址（优先取活跃钱包，无则取任意含地址的历史钱包）
    wallet = (await db.execute(
        select(Wallet).where(
            Wallet.chain == collection.chain,
            Wallet.type == "collection",
            Wallet.is_active == True,
        )
    )).scalar_one_or_none()
    if not wallet:
        # 兼容历史记录：钱包已软删除，尝试取历史记录
        wallet = (await db.execute(
            select(Wallet).where(
                Wallet.chain == collection.chain,
                Wallet.type == "collection",
                Wallet.address.isnot(None),
            ).order_by(Wallet.id.desc()).limit(1)
        )).scalar_one_or_none()
    if wallet:
        out.target_address = wallet.address

    return out


# ─── 执行进度 ────────────────────────────────────────

@router.get("/{collection_id}/progress")
async def get_collection_progress(
    collection_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageCollections],
):
    """查询归集执行进度（实时）"""
    from app.services.collection_executor import get_collection_progress as _get_progress

    collection = (await db.execute(
        select(Collection).where(Collection.id == collection_id)
    )).scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="归集记录不存在")

    # 从内存获取实时进度
    progress = _get_progress(collection_id)

    # 从 DB 统计各状态数量（更准确）
    items = (await db.execute(
        select(CollectionItem).where(CollectionItem.collection_id == collection_id)
    )).scalars().all()

    status_counts = {}
    for item in items:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1

    return {
        "collection_id": collection_id,
        "collection_status": collection.status,
        "total": len(items),
        "status_counts": status_counts,
        "completed": status_counts.get("completed", 0),
        "failed": status_counts.get("failed", 0),
        "pending": status_counts.get("pending", 0),
        "gas_sent": status_counts.get("gas_sent", 0),
        "transferring": status_counts.get("transferring", 0),
        # 内存级实时进度（执行中才有）
        "realtime": progress,
    }
