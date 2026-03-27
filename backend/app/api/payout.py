"""
批量打款 API

POST /payouts         创建打款批次 + 关联多签提案（等待 2/3 签名后执行）
GET  /payouts         列表（分页）
GET  /payouts/{id}    详情 + 明细
GET  /payouts/{id}/progress  实时执行进度
GET  /payouts/{id}/export    导出 CSV
POST /payouts/precheck       余额预检（创建前估算）
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.payout import Payout, PayoutItem
from app.models.proposal import Proposal
from app.models.wallet import Wallet
from app.core.telegram import notifier
from app.core.deps import require_module
from app.services.chain_client import chain_client
from app.schemas.payout import (
    PayoutCreate, PayoutOut, PayoutItemOut, PayoutListResponse,
    PayoutPrecheck, PayoutPrecheckResult,
)
from app.services.proposal_service import proposal_service
from app.services.tron_energy import tron_energy_service
from app.services.payout_executor import execute_payout

# 能量预检估算上限：100k 覆盖有余额地址的保守情况（实际通常 65k-75k）
# 全新地址（需建存储槽）约需 160k，但预检用较低值避免过于保守拦截正常打款
# 实际执行时会用 estimate_transfer_energy() 动态估算，这里只是创建/预检时的余额门槛
_ENERGY_PRECHECK_CONSERVATIVE = 100_000

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payouts", tags=["批量打款"])

CanManagePayouts = Depends(require_module("payouts"))


# ─── 余额预检 ─────────────────────────────────────────

@router.post("/precheck", response_model=PayoutPrecheckResult)
async def precheck_payout(
    body: PayoutPrecheck,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManagePayouts],
):
    """创建打款前的余额预检：USDT 是否足够 + gas 费估算"""
    wallet = (await db.execute(
        select(Wallet).where(Wallet.id == body.wallet_id)
    )).scalar_one_or_none()
    if not wallet or not wallet.address:
        raise HTTPException(status_code=400, detail="打款钱包不存在")

    chain = body.chain
    asset_type = body.asset_type
    total_amount = sum(it.amount for it in body.items)
    n = len(body.items)

    try:
        usdt_balance = await chain_client.get_usdt_balance(chain, wallet.address)
        # TRON 多签打款：实际可用余额 = 多签钱包 + 中转钱包（中转钱包可能已有余额）
        if chain == "TRON" and wallet.is_multisig and wallet.relay_wallet_id:
            relay_w = (await db.execute(
                select(Wallet).where(Wallet.id == wallet.relay_wallet_id)
            )).scalar_one_or_none()
            if relay_w and relay_w.address:
                try:
                    relay_bal = await chain_client.get_usdt_balance("TRON", relay_w.address)
                    usdt_balance += relay_bal
                except Exception:
                    pass
    except Exception:
        usdt_balance = Decimal(0)

    # 估算 gas 费
    gas_auto_supplement = False
    feee_balance_trx: Decimal | None = None
    feee_balance_sufficient: bool | None = None
    estimated_feee_cost: Decimal | None = None
    rental_enabled = False
    if chain == "BSC":
        try:
            from app.services.chain_client import RPCManager
            rpc = RPCManager.get_instance("BSC")
            gas_price_wei = rpc.get_gas_price()
            estimated_gas = Decimal(n * 65000) * Decimal(gas_price_wei) / Decimal("1000000000000000000")
        except Exception:
            estimated_gas = Decimal(str(n)) * Decimal("0.0005")
        energy_cost_trx = None
        try:
            native_balance = await chain_client.get_native_balance(chain, wallet.address)
        except Exception:
            native_balance = Decimal(0)
    else:
        # TRON：根据能量租赁是否启用估算 Gas 钱包所需 TRX
        try:
            s = await chain_client._load_settings()
            rental_enabled = s.tron_energy_rental_enabled and bool((s.tron_energy_rental_api_url or "").strip())
        except Exception:
            rental_enabled = False

        if rental_enabled:
            # 能量租赁启用：feee.io 覆盖能量费，Gas 钱包只需带宽缓冲
            from app.services.chain_client import GAS_ESTIMATE_TRON, GAS_BUFFER_MULTIPLIER
            energy_cost_trx = None
            estimated_gas = GAS_ESTIMATE_TRON * GAS_BUFFER_MULTIPLIER * n
            rental_api_url = (s.tron_energy_rental_api_url or "").strip()
            rental_api_key = (s.tron_energy_rental_api_key or "").strip()
            usdt_contract = (s.tron_usdt_contract or "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").strip()
            rental_max_price = s.tron_energy_rental_max_price or 150
            # 用 feee.io estimate_energy 接口估算每笔实际费用
            estimated_feee_cost = Decimal(0)
            for item in body.items:
                est = await tron_energy_service.estimate_fee(
                    rental_api_url, rental_api_key,
                    wallet.address, item.to_address, usdt_contract,
                )
                if est:
                    estimated_feee_cost += Decimal(str(est["fee"]))
                else:
                    # feee.io 估算失败，回退到保守算法
                    estimated_feee_cost += Decimal(_ENERGY_PRECHECK_CONSERVATIVE) * Decimal(rental_max_price) / Decimal(1_000_000)
            # 查询 feee.io 账户余额
            feee_balance_trx = await tron_energy_service.get_feee_balance(rental_api_url, rental_api_key)
            feee_balance_sufficient = feee_balance_trx >= estimated_feee_cost if feee_balance_trx is not None else None
        else:
            # 未启用租赁：保守估算（75k energy × 420 sun = 31.5 TRX/笔 + 带宽 0.35 TRX/笔）
            energy_cost_trx = Decimal(str(n)) * Decimal("31.5")
            bandwidth_cost = Decimal(str(n)) * Decimal("0.35")
            estimated_gas = energy_cost_trx + bandwidth_cost

        if asset_type == "usdt":
            # TRON USDT：TRX 由系统 Gas 钱包自动补充，检查 Gas 钱包余额
            gas_auto_supplement = True
            tron_gas_wallet = (await db.execute(
                select(Wallet).where(Wallet.chain == "TRON", Wallet.type == "gas")
            )).scalars().first()
            if tron_gas_wallet and tron_gas_wallet.address:
                try:
                    native_balance = await chain_client.get_native_balance("TRON", tron_gas_wallet.address)
                except Exception:
                    native_balance = Decimal(0)
            else:
                native_balance = Decimal(0)
        else:
            # TRON native：打款钱包需自带 TRX（转账额 + 手续费）
            try:
                native_balance = await chain_client.get_native_balance(chain, wallet.address)
            except Exception:
                native_balance = Decimal(0)

    if asset_type == "usdt":
        usdt_sufficient = usdt_balance >= total_amount
        native_sufficient = native_balance >= estimated_gas
    else:  # native 转账：需要转账金额 + gas 费
        usdt_sufficient = True
        native_sufficient = native_balance >= (total_amount + estimated_gas)
        estimated_gas = total_amount + estimated_gas

    feee_ok = feee_balance_sufficient is not False  # None（查不到）视为通过
    return PayoutPrecheckResult(
        ok=usdt_sufficient and native_sufficient and feee_ok,
        total_amount=total_amount,
        usdt_balance=usdt_balance,
        usdt_sufficient=usdt_sufficient,
        estimated_gas_native=estimated_gas,
        native_balance=native_balance,
        native_sufficient=native_sufficient,
        gas_auto_supplement=gas_auto_supplement,
        estimated_energy_cost_trx=energy_cost_trx,
        feee_balance_trx=feee_balance_trx,
        feee_balance_sufficient=feee_balance_sufficient,
        estimated_feee_cost_trx=estimated_feee_cost,
    )


# ─── 创建打款批次 ─────────────────────────────────────

@router.post("", response_model=PayoutOut, status_code=201)
async def create_payout(
    body: PayoutCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManagePayouts],
):
    """创建批量打款批次，同时创建关联多签提案（等待 2/3 签名后自动执行）"""
    chain = body.chain

    # 校验打款钱包
    wallet = (await db.execute(
        select(Wallet).where(Wallet.id == body.wallet_id)
    )).scalar_one_or_none()
    if not wallet or not wallet.address:
        raise HTTPException(status_code=400, detail="打款钱包不存在")
    if wallet.chain != chain:
        raise HTTPException(status_code=400, detail="打款钱包链不匹配")
    if wallet.type != "payout":
        raise HTTPException(status_code=400, detail="只允许使用打款钱包")

    total_amount = sum(it.amount for it in body.items)

    # 余额预检（硬校验）
    try:
        if body.asset_type == "usdt":
            usdt_balance = await chain_client.get_usdt_balance(chain, wallet.address)
            # TRON 多签：可用余额 = 多签钱包 + 中转钱包（执行时会自动计算差额转移）
            if chain == "TRON" and wallet.is_multisig and wallet.relay_wallet_id:
                _relay = (await db.execute(
                    select(Wallet).where(Wallet.id == wallet.relay_wallet_id)
                )).scalar_one_or_none()
                if _relay and _relay.address:
                    try:
                        usdt_balance += await chain_client.get_usdt_balance("TRON", _relay.address)
                    except Exception:
                        pass
            if usdt_balance < total_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"USDT 余额不足：当前 {float(usdt_balance):.2f}，需要 {float(total_amount):.2f}",
                )
        else:  # native
            native_balance = await chain_client.get_native_balance(chain, wallet.address)
            native_label = "BNB" if chain == "BSC" else "TRX"
            if native_balance < total_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"{native_label} 余额不足：当前 {float(native_balance):.6f}，需要 {float(total_amount):.6f}",
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("余额预检查询失败，跳过硬校验: %s", e)

    # 创建 Payout 批次
    payout = Payout(
        chain=chain,
        asset_type=body.asset_type,
        status="pending",
        total_amount=total_amount,
        item_count=len(body.items),
        wallet_id=wallet.id,
        memo=body.memo,
        created_by=current_user.id,
    )
    db.add(payout)
    await db.flush()

    # 创建 PayoutItems
    item_objs = []
    for item in body.items:
        obj = PayoutItem(
            payout_id=payout.id,
            to_address=item.to_address.strip(),
            amount=item.amount,
            memo=item.memo,
            status="pending",
        )
        db.add(obj)
        item_objs.append(obj)

    # 构建 Safe tx 数据和签名 hash
    settings = await chain_client._load_settings()

    if chain == "BSC" and wallet.is_multisig:
        # BSC 多签：构建真实 Safe MultiSend tx，EIP-712 签名
        tx_data = await proposal_service.build_bsc_safe_multisend_tx(
            safe_address=wallet.address,
            items=item_objs,
            asset_type=body.asset_type,
            usdt_contract=settings.bsc_usdt_contract,
        )
        tx_data["_payout_id"] = payout.id
        tx_data["_total_amount"] = str(total_amount)
        tx_data["_item_count"] = len(body.items)
        safe_tx_hash = proposal_service.compute_safe_tx_hash(wallet.address, tx_data)
        proposal_tx_data = json.dumps(tx_data, default=str)

    elif chain == "TRON" and wallet.is_multisig:
        # TRON 多签：提案内容为「转 USDT/TRX 到中转钱包」，签名达阈值后执行该笔转账，再由中转钱包分发
        if not wallet.relay_wallet_id:
            raise HTTPException(status_code=400, detail="TRON 多签打款钱包缺少中转钱包，请重新创建钱包")
        relay_wallet = (await db.execute(
            select(Wallet).where(Wallet.id == wallet.relay_wallet_id)
        )).scalar_one_or_none()
        if not relay_wallet or not relay_wallet.address:
            raise HTTPException(status_code=400, detail="中转钱包不存在或地址为空")

        # 查中转钱包已有余额，计算实际需从多签转出的差额
        relay_balance = Decimal(0)
        try:
            if body.asset_type == "usdt":
                relay_balance = await chain_client.get_usdt_balance("TRON", relay_wallet.address)
            else:
                relay_balance = await chain_client.get_native_balance("TRON", relay_wallet.address)
        except Exception as _rbe:
            logger.warning("查询中转钱包余额失败，按全额创建提案: %s", _rbe)

        transfer_amount = max(Decimal(0), total_amount - relay_balance)
        if relay_balance > 0:
            logger.info("中转钱包已有余额 %s，实际需转 %s（总需 %s）", relay_balance, transfer_amount, total_amount)

        # feee.io 余额预检：transfer_amount>0 时多签→中转需 +1 笔
        if body.asset_type == "usdt" and settings.tron_energy_rental_enabled and settings.tron_energy_rental_api_url:
            try:
                feee_balance = await tron_energy_service.get_feee_balance(
                    settings.tron_energy_rental_api_url,
                    settings.tron_energy_rental_api_key or "",
                )
                if feee_balance is not None:
                    needed_rentals = len(body.items) + (1 if transfer_amount > 0 else 0)
                    max_price = settings.tron_energy_rental_max_price or 420
                    min_needed_trx = Decimal(needed_rentals * _ENERGY_PRECHECK_CONSERVATIVE * max_price) / Decimal(1_000_000)
                    if feee_balance < min_needed_trx:
                        raise HTTPException(
                            status_code=400,
                            detail=f"feee.io 余额不足：当前 {feee_balance:.2f} TRX，预计需要 {min_needed_trx:.2f} TRX（{needed_rentals} 笔 × {_ENERGY_PRECHECK_CONSERVATIVE} energy）",
                        )
                    logger.info("feee.io 余额预检通过: %.2f TRX（需 %.2f）", feee_balance, min_needed_trx)
            except HTTPException:
                raise
            except Exception as _fe:
                logger.warning("feee.io 余额预检失败（不阻断创建）: %s", _fe)

        # 构建 TRON 多签转账交易
        _is_contract_multisig = wallet.derive_index is None  # 合约多签无 HD key
        import hashlib as _hashlib
        if transfer_amount > 0:
            # 中转余额不足：建链上转账 tx（仅转差额），签名后广播
            if _is_contract_multisig:
                # 合约多签（TronMultiSig.sol）：调用 getMessageHash 生成待签哈希
                if body.asset_type != "usdt":
                    raise HTTPException(status_code=400, detail="TRON 合约多签打款仅支持 USDT")
                tx_data = await proposal_service.build_tron_contract_proposal(
                    contract_address=wallet.address,
                    token_address=settings.tron_usdt_contract,
                    to_address=relay_wallet.address,
                    amount=transfer_amount,
                )
            elif body.asset_type == "usdt":
                tx_data = await proposal_service.build_tron_multisig_tx(
                    owner_address=wallet.address,
                    to_address=relay_wallet.address,
                    amount=transfer_amount,
                    usdt_contract=settings.tron_usdt_contract,
                )
            else:  # native TRX
                tx_data = await proposal_service.build_tron_multisig_native_tx(
                    owner_address=wallet.address,
                    to_address=relay_wallet.address,
                    amount=transfer_amount,
                )
        else:
            # 中转余额已足够：建纯审批提案（无需链上转账），签名后直接执行打款
            _approval_data: dict = {
                "_no_transfer": True,
                "_payout_id": payout.id,
                "_total_amount": str(total_amount),
                "_relay_wallet_id": relay_wallet.id,
                "_relay_wallet_address": relay_wallet.address,
            }
            tx_data = _approval_data
        tx_data["_payout_id"] = payout.id
        tx_data["_total_amount"] = str(total_amount)
        tx_data["_item_count"] = len(body.items)
        tx_data["_relay_wallet_id"] = relay_wallet.id
        tx_data["_relay_wallet_address"] = relay_wallet.address
        if tx_data.get("_no_transfer"):
            # 纯审批提案：用内容的 SHA256 作为唯一标识
            safe_tx_hash = "0x" + _hashlib.sha256(
                json.dumps(tx_data, sort_keys=True, default=str).encode()
            ).hexdigest()
        elif tx_data.get("_contract_multisig"):
            # 合约多签：safe_tx_hash = msg_hash（由 getMessageHash 计算）
            safe_tx_hash = tx_data["msg_hash"]
        else:
            safe_tx_hash = tx_data.get("txID", "")
            if not safe_tx_hash.startswith("0x"):
                safe_tx_hash = "0x" + safe_tx_hash
        proposal_tx_data = json.dumps(tx_data, default=str)

    else:
        # 普通钱包（有 derive_index）：SHA256 摘要，执行时直接走 execute_payout
        payout_data = {
            "payout_id": payout.id,
            "chain": chain,
            "wallet_address": wallet.address,
            "total_amount": str(total_amount),
            "item_count": len(body.items),
        }
        safe_tx_hash = "0x" + hashlib.sha256(
            json.dumps(payout_data, sort_keys=True).encode()
        ).hexdigest()
        proposal_tx_data = json.dumps(payout_data, default=str)

    # 创建关联多签提案（type="payout_batch"），等待签名达阈值后执行
    proposal = Proposal(
        chain=chain,
        type="payout_batch",
        status="pending",
        title=f"{chain} 批量打款: {len(body.items)} 笔, {total_amount} {body.asset_type.upper()}",
        description=f"从 {wallet.address[:10]}... 打款给 {len(body.items)} 个地址",
        wallet_id=wallet.id,
        tx_data=proposal_tx_data,
        safe_tx_hash=safe_tx_hash,
        threshold=wallet.threshold or 2,
        current_signatures=0,
        created_by=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
    )
    db.add(proposal)
    await db.flush()
    await db.refresh(proposal)
    await db.refresh(payout)

    payout.proposal_id = proposal.id

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="create_payout",
        detail=f"创建{chain}打款批次 #{payout.id}: {len(body.items)} 笔, {total_amount} {body.asset_type.upper()}, 提案#{proposal.id}",
        ip_address=request.client.host if request.client else None,
    ))

    asyncio.create_task(notifier.notify_payout_batch_created(
        chain=chain,
        wallet_address=wallet.address,
        item_count=len(body.items),
        total_amount=total_amount,
        memo=body.memo or "",
    ))
    asyncio.create_task(notifier.notify_proposal_created(
        chain=chain,
        proposal_type="payout",
        title=proposal.title or "",
        threshold=wallet.threshold or 2,
        creator_name=current_user.username,
    ))

    return await _build_payout_out(db, payout, wallet, current_user)


# ─── 列表 ────────────────────────────────────────────

@router.get("", response_model=PayoutListResponse)
async def list_payouts(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManagePayouts],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    chain: str | None = None,
    status: str | None = None,
):
    query = select(Payout)
    count_q = select(func.count(Payout.id))

    if chain:
        query = query.where(Payout.chain == chain.upper())
        count_q = count_q.where(Payout.chain == chain.upper())
    if status:
        query = query.where(Payout.status == status)
        count_q = count_q.where(Payout.status == status)

    total = (await db.execute(count_q)).scalar() or 0
    query = query.order_by(Payout.id.desc()).offset((page - 1) * page_size).limit(page_size)
    payouts = (await db.execute(query)).scalars().all()

    items = []
    for p in payouts:
        wallet = (await db.execute(select(Wallet).where(Wallet.id == p.wallet_id))).scalar_one_or_none()
        items.append(await _build_payout_out(db, p, wallet, current_user, include_items=False))

    return PayoutListResponse(items=items, total=total, page=page, page_size=page_size)


# ─── 详情 ────────────────────────────────────────────

@router.get("/{payout_id}", response_model=PayoutOut)
async def get_payout_detail(
    payout_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManagePayouts],
):
    payout = (await db.execute(
        select(Payout).where(Payout.id == payout_id)
    )).scalar_one_or_none()
    if not payout:
        raise HTTPException(status_code=404, detail="打款批次不存在")
    wallet = (await db.execute(select(Wallet).where(Wallet.id == payout.wallet_id))).scalar_one_or_none()
    return await _build_payout_out(db, payout, wallet, current_user, include_items=True)


# ─── 执行进度 ────────────────────────────────────────

@router.get("/{payout_id}/progress")
async def get_payout_progress(
    payout_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManagePayouts],
):
    from app.services.payout_executor import get_payout_progress as _get_progress  # lazy to avoid circular
    payout = (await db.execute(select(Payout).where(Payout.id == payout_id))).scalar_one_or_none()
    if not payout:
        raise HTTPException(status_code=404, detail="打款批次不存在")

    items = (await db.execute(
        select(PayoutItem).where(PayoutItem.payout_id == payout_id)
    )).scalars().all()

    status_counts: dict[str, int] = {}
    for item in items:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1

    return {
        "payout_id": payout_id,
        "payout_status": payout.status,
        "total": len(items),
        "status_counts": status_counts,
        "completed": status_counts.get("completed", 0),
        "failed": status_counts.get("failed", 0),
        "pending": status_counts.get("pending", 0),
        "processing": status_counts.get("processing", 0),
        "realtime": _get_progress(payout_id),
    }


# ─── 导出 CSV ─────────────────────────────────────────

@router.get("/{payout_id}/export")
async def export_payout_csv(
    payout_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManagePayouts],
    status: str | None = Query(None, description="筛选状态，如 failed"),
):
    """导出打款明细 CSV（可筛选失败条目）"""
    payout = (await db.execute(select(Payout).where(Payout.id == payout_id))).scalar_one_or_none()
    if not payout:
        raise HTTPException(status_code=404, detail="打款批次不存在")

    query = select(PayoutItem).where(PayoutItem.payout_id == payout_id)
    if status:
        query = query.where(PayoutItem.status == status)
    query = query.order_by(PayoutItem.id)
    items = (await db.execute(query)).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "目标地址", "金额", "备注", "状态", "txHash", "错误信息", "重试次数"])
    for item in items:
        writer.writerow([
            item.id,
            item.to_address,
            str(item.amount),
            item.memo or "",
            item.status,
            item.tx_hash or "",
            item.error_message or "",
            item.retry_count,
        ])

    output.seek(0)
    filename = f"payout_{payout_id}{'_failed' if status == 'failed' else ''}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

# ─── 内部辅助 ─────────────────────────────────────────

async def _build_payout_out(
    db: AsyncSession,
    payout: Payout,
    wallet: Wallet | None,
    current_user: Admin,
    include_items: bool = False,
) -> PayoutOut:
    items = None
    if include_items:
        rows = (await db.execute(
            select(PayoutItem).where(PayoutItem.payout_id == payout.id).order_by(PayoutItem.id)
        )).scalars().all()
        items = [PayoutItemOut.model_validate(r) for r in rows]

    return PayoutOut(
        id=payout.id,
        chain=payout.chain,
        asset_type=payout.asset_type,
        status=payout.status,
        total_amount=payout.total_amount,
        item_count=payout.item_count,
        wallet_id=payout.wallet_id,
        wallet_address=wallet.address if wallet else None,
        memo=payout.memo,
        proposal_id=payout.proposal_id,
        created_by=payout.created_by,
        created_by_username=current_user.username,
        executed_at=payout.executed_at,
        created_at=payout.created_at,
        updated_at=payout.updated_at,
        items=items,
    )
