"""
提案签名 API — 创建 / 列表 / 详情 / 签名 / 拒绝
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models.admin import Admin
from app.models.collection import Collection
from app.models.payout import Payout, PayoutItem
from app.models.wallet import Wallet
from app.models.proposal import Proposal, Signature
from app.models.audit_log import AuditLog
from app.core.deps import require_module
from app.core.hdwallet import get_private_key
from app.services.chain_client import chain_client
from app.services.proposal_service import proposal_service
from app.services.collection_executor import execute_collection
from app.services.payout_executor import execute_payout
from app.services.tron_energy import tron_energy_service, estimate_transfer_energy
from app.schemas.proposal import (
    ProposalCreate, ProposalSign,
    ProposalOut, ProposalListResponse, SignatureOut, SignResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proposals", tags=["提案签名"])

CanManageMultisig = Depends(require_module("multisig"))


# ─── 辅助：构建 ProposalOut ──────────────────────────

async def _build_proposal_out(
    db: AsyncSession,
    proposal: Proposal,
    wallet: Wallet | None = None,
) -> ProposalOut:
    """从 Proposal 模型构建完整的 ProposalOut 响应"""

    # 加载钱包信息
    if wallet is None and proposal.wallet_id:
        wallet = (await db.execute(
            select(Wallet).where(Wallet.id == proposal.wallet_id)
        )).scalar_one_or_none()

    # 加载签名列表
    sigs_result = await db.execute(
        select(Signature).where(Signature.proposal_id == proposal.id)
    )
    sigs = sigs_result.scalars().all()

    # 查询签名者用户名
    sig_outs = []
    for sig in sigs:
        admin = (await db.execute(
            select(Admin).where(Admin.id == sig.signer_id)
        )).scalar_one_or_none()
        sig_outs.append(SignatureOut(
            id=sig.id,
            signer_id=sig.signer_id,
            signer_address=sig.signer_address,
            signer_username=admin.username if admin else None,
            signed_at=sig.signed_at,
        ))

    # 创建者用户名
    creator = (await db.execute(
        select(Admin).where(Admin.id == proposal.created_by)
    )).scalar_one_or_none()

    # 解析 tx_data
    tx_data = None
    to_address = None
    amount = None
    if proposal.tx_data:
        try:
            tx_data = json.loads(proposal.tx_data)
            to_address = tx_data.get("_to_address")
            amount = tx_data.get("_amount")
        except (json.JSONDecodeError, TypeError):
            pass

    return ProposalOut(
        id=proposal.id,
        chain=proposal.chain,
        type=proposal.type,
        status=proposal.status,
        title=proposal.title,
        description=proposal.description,
        wallet_id=proposal.wallet_id,
        wallet_address=wallet.address if wallet else None,
        to_address=to_address,
        amount=amount,
        safe_tx_hash=proposal.safe_tx_hash,
        tx_data=tx_data,
        threshold=proposal.threshold,
        current_signatures=proposal.current_signatures,
        owners=wallet.owners if wallet else None,
        signatures=sig_outs,
        created_by=proposal.created_by,
        created_by_username=creator.username if creator else None,
        execution_tx_hash=proposal.execution_tx_hash,
        executed_at=proposal.executed_at,
        expires_at=proposal.expires_at,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


# ─── POST / — 创建提案 ──────────────────────────────

@router.post("", response_model=ProposalOut, status_code=201)
async def create_proposal(
    body: ProposalCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageMultisig],
):
    """创建多签转账提案"""

    # 1. 加载多签钱包
    wallet = (await db.execute(
        select(Wallet).where(Wallet.id == body.wallet_id)
    )).scalar_one_or_none()

    if not wallet:
        raise HTTPException(status_code=404, detail="钱包不存在")
    if not wallet.is_multisig:
        raise HTTPException(status_code=400, detail="不是多签钱包")
    if wallet.multisig_status != "active":
        raise HTTPException(status_code=400, detail=f"钱包状态不可用: {wallet.multisig_status}")
    if wallet.chain != body.chain:
        raise HTTPException(status_code=400, detail="链不匹配")

    # 2. 构建链上交易
    from app.models.system_settings import SystemSettings
    settings = (await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )).scalar_one_or_none()
    if not settings:
        raise HTTPException(status_code=500, detail="系统设置未初始化")

    token = getattr(body, "token", "usdt") or "usdt"

    try:
        if body.chain == "BSC":
            if token == "native":
                tx_data = await proposal_service.build_bsc_safe_native_tx(
                    wallet.address, body.to_address, body.amount,
                )
            else:
                tx_data = await proposal_service.build_bsc_safe_tx(
                    wallet.address, body.to_address, body.amount,
                    settings.bsc_usdt_contract,
                )
            safe_tx_hash = proposal_service.compute_safe_tx_hash(
                wallet.address, tx_data,
            )
        elif wallet.is_multisig and wallet.derive_index is None:
            # ─── TRON 合约多签（TronMultiSig.sol）───
            # 合约钱包无 HD key，derive_index=None，签名用 signMessageV2
            if token == "native":
                raise HTTPException(status_code=400, detail="TRON 合约多签暂不支持 TRX 原生转账，请使用 USDT")
            tx_data = await proposal_service.build_tron_contract_proposal(
                wallet.address, settings.tron_usdt_contract,
                body.to_address, body.amount,
            )
            safe_tx_hash = tx_data["msg_hash"]
        else:
            # ─── TRON 原生多签（accountpermissionupdate）───
            if token == "native":
                tx_data = await proposal_service.build_tron_multisig_native_tx(
                    wallet.address, body.to_address, body.amount,
                )
            else:
                tx_data = await proposal_service.build_tron_multisig_tx(
                    wallet.address, body.to_address, body.amount,
                    settings.tron_usdt_contract,
                )
            safe_tx_hash = proposal_service.compute_tron_tx_hash(
                tx_data["raw_data_hex"],
            )
            # 确保多签钱包有足够 TRX 用于带宽（TP 签名模拟需要）
            # 如果余额不足 2 TRX，从 gas 钱包补充 5 TRX
            try:
                from app.services.chain_client import ChainClient
                chain_client = ChainClient()
                tron_gas = (await db.execute(
                    select(Wallet).where(
                        Wallet.chain == "TRON", Wallet.type == "gas",
                    )
                )).scalars().first()
                if tron_gas and tron_gas.derive_index is not None:
                    trx_balance = await chain_client._tron_native_balance(
                        settings.tron_api_urls, settings.tron_api_keys, wallet.address,
                    )
                    if trx_balance < 2:
                        gas_key = get_private_key("TRON", tron_gas.derive_index)
                        gas_tx = await chain_client._tron_send_native(
                            settings.tron_api_urls, settings.tron_api_keys,
                            gas_key, tron_gas.address, wallet.address,
                            __import__('decimal').Decimal("5"),
                        )
                        tx_data["_gas_tx_hash"] = gas_tx
                        tx_data["_gas_amount"] = "5 TRX"
                        tx_data["_gas_from"] = tron_gas.address
                        logger.info("已从 gas 钱包补充 5 TRX 到多签钱包 %s，tx: %s", wallet.address, gas_tx)
            except Exception as e:
                logger.warning("补充多签钱包 TRX 失败（不阻断）: %s", e)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"交易构建失败: {e}")

    # 存入额外信息
    tx_data["_to_address"] = body.to_address
    tx_data["_amount"] = str(body.amount)
    tx_data["_token"] = token
    tx_data["_memo"] = body.memo
    tx_data["_wallet_id"] = wallet.id

    # 3. 创建提案
    proposal = Proposal(
        chain=body.chain,
        type=body.type,
        status="pending",
        title=body.title,
        description=body.description,
        wallet_id=wallet.id,
        tx_data=json.dumps(tx_data, default=str),
        safe_tx_hash=safe_tx_hash,
        threshold=wallet.threshold or 2,
        current_signatures=0,
        created_by=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(proposal)
    await db.flush()
    await db.refresh(proposal)

    # 4. 审计日志
    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="create_proposal",
        detail=f"创建提案 #{proposal.id}: {body.chain} {body.type} {body.amount} USDT → {body.to_address[:10]}...",
        ip_address=request.client.host if request.client else None,
    ))

    # TG 通知
    from app.core.telegram import notifier
    asyncio.create_task(notifier.notify_proposal_created(
        chain=body.chain,
        proposal_type=body.type,
        title=proposal.title or "",
        threshold=wallet.threshold,
        creator_name=current_user.username,
    ))

    return await _build_proposal_out(db, proposal, wallet)


# ─── GET / — 提案列表 ───────────────────────────────

@router.get("", response_model=ProposalListResponse)
async def list_proposals(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageMultisig],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    chain: str | None = None,
    status: str | None = None,
    type: str | None = None,
):
    """提案列表（分页 + 筛选）"""

    query = select(Proposal)
    count_query = select(sa_func.count(Proposal.id))

    if chain:
        query = query.where(Proposal.chain == chain)
        count_query = count_query.where(Proposal.chain == chain)
    if status:
        query = query.where(Proposal.status == status)
        count_query = count_query.where(Proposal.status == status)
    if type:
        query = query.where(Proposal.type == type)
        count_query = count_query.where(Proposal.type == type)

    total = (await db.execute(count_query)).scalar() or 0

    proposals = (await db.execute(
        query.order_by(Proposal.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    items = []
    for p in proposals:
        items.append(await _build_proposal_out(db, p))

    return ProposalListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── GET /{id} — 提案详情 ───────────────────────────

@router.get("/{proposal_id}", response_model=ProposalOut)
async def get_proposal_detail(
    proposal_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageMultisig],
):
    """获取提案详情"""

    proposal = (await db.execute(
        select(Proposal).where(Proposal.id == proposal_id)
    )).scalar_one_or_none()

    if not proposal:
        raise HTTPException(status_code=404, detail="提案不存在")

    return await _build_proposal_out(db, proposal)


# ─── POST /{id}/sign — 提交签名 ─────────────────────

@router.post("/{proposal_id}/sign", response_model=SignResult)
async def sign_proposal(
    proposal_id: int,
    body: ProposalSign,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageMultisig],
):
    """提交签名"""

    # 1. 加载提案
    proposal = (await db.execute(
        select(Proposal).where(Proposal.id == proposal_id)
    )).scalar_one_or_none()

    if not proposal:
        raise HTTPException(status_code=404, detail="提案不存在")

    if proposal.status not in ("pending", "signing", "executing"):
        raise HTTPException(status_code=400, detail=f"提案状态不允许签名: {proposal.status}")

    # 2. 检查过期
    if proposal.expires_at and datetime.now(timezone.utc) > proposal.expires_at:
        proposal.status = "expired"
        await db.flush()
        raise HTTPException(status_code=400, detail="提案已过期")

    # 3. 加载钱包
    wallet = (await db.execute(
        select(Wallet).where(Wallet.id == proposal.wallet_id)
    )).scalar_one_or_none()

    if not wallet:
        raise HTTPException(status_code=400, detail="关联钱包不存在")

    # 4. 验证签名人是 owner
    signer_addr = body.signer_address
    owners = wallet.owners or []

    if proposal.chain == "BSC":
        # BSC 地址不区分大小写
        is_owner = signer_addr.lower() in [o.lower() for o in owners]
    else:
        is_owner = signer_addr in owners

    if not is_owner:
        raise HTTPException(status_code=403, detail="签名地址不是该钱包的 owner")

    # 5. 防重复签名
    existing = (await db.execute(
        select(Signature).where(
            Signature.proposal_id == proposal_id,
            Signature.signer_address == signer_addr,
        )
    )).scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=409, detail="您已经签署过此提案")

    # 7. 验证签名密码学正确性
    tx_data = json.loads(proposal.tx_data) if proposal.tx_data else {}

    if proposal.type == "collection":
        # 归集提案：签名的是 sha256 摘要（personal_sign / tronWeb.trx.signMessageV2）
        valid = proposal_service.verify_bsc_signature(
            proposal.safe_tx_hash, body.signature, signer_addr,
        ) if proposal.chain == "BSC" else proposal_service.verify_collection_signature_tron(
            proposal.safe_tx_hash, body.signature, signer_addr,
        )
    elif proposal.chain == "BSC":
        valid = proposal_service.verify_bsc_signature(
            proposal.safe_tx_hash, body.signature, signer_addr,
        )
    elif tx_data.get("_no_transfer") or tx_data.get("_contract_multisig"):
        # 纯审批提案 或 合约多签提案：用 safe_tx_hash + signMessageV2 验证
        valid = proposal_service.verify_collection_signature_tron(
            proposal.safe_tx_hash, body.signature, signer_addr,
        )
    else:
        # TRON 原生多签转账提案：前端用 tronWeb.trx.sign(transaction) 直接签名
        raw_data_hex = tx_data.get("raw_data_hex", "")
        # TronLink 手机版会刷新 expiration，导致 raw_data_hex 变化
        # 如果前端传回了实际签名用的 raw_data_hex，优先使用
        if body.signed_raw_data_hex:
            raw_data_hex = body.signed_raw_data_hex
            logger.info("[sign] 使用前端传回的 signed_raw_data_hex (len=%d)", len(raw_data_hex))
        # 从签名恢复实际签名人地址（比对声明地址）
        logger.info("[sign] FULL_SIG=%s", body.signature)
        recovered_signer = proposal_service.recover_tron_signer(raw_data_hex, body.signature)
        logger.info(
            "[sign] TRON tx proposal #%d claimed=%s recovered=%s",
            proposal_id, signer_addr, recovered_signer,
        )
        if recovered_signer and recovered_signer != signer_addr:
            # 手机 TronLink 可能签名账户与 defaultAddress 不一致
            # 如果恢复出的地址也是合法 owner，接受并替换 signer_addr
            owners = wallet.owners or []
            if recovered_signer in owners:
                logger.info("[sign] 使用恢复地址 %s 替代声明地址 %s", recovered_signer, signer_addr)
                # 检查恢复地址是否已签名
                existing_recovered = (await db.execute(
                    select(Signature).where(
                        Signature.proposal_id == proposal_id,
                        Signature.signer_address == recovered_signer,
                    )
                )).scalar_one_or_none()
                if existing_recovered:
                    raise HTTPException(status_code=409, detail="您已经签署过此提案")
                signer_addr = recovered_signer
                valid = True
            else:
                logger.warning("[sign] 恢复地址 %s 不在 owners 列表", recovered_signer)
                valid = False
        else:
            valid = recovered_signer == signer_addr

    if not valid:
        raise HTTPException(status_code=400, detail="签名验证失败（请确认手机TronLink账户与注册owner地址一致）")

    # 8. 存储签名
    sig = Signature(
        proposal_id=proposal_id,
        signer_id=current_user.id,
        signer_address=signer_addr,
        signature=body.signature,
    )
    db.add(sig)

    proposal.current_signatures += 1
    if proposal.status == "pending":
        proposal.status = "signing"

    # 9. 审计日志
    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="sign_proposal",
        detail=f"签署提案 #{proposal_id} ({proposal.current_signatures}/{proposal.threshold})",
        ip_address=request.client.host if request.client else None,
    ))

    # TG 签名通知
    from app.core.telegram import notifier as _notifier
    asyncio.create_task(_notifier.notify_proposal_signed(
        chain=proposal.chain,
        title=proposal.title or "",
        signer_name=current_user.username,
        current_signatures=proposal.current_signatures,
        threshold=proposal.threshold,
    ))

    # 10. 检查是否达到阈值 → 自动执行
    auto_executed = False
    execution_tx_hash = None
    execution_error: str | None = None

    if proposal.current_signatures >= proposal.threshold:
        # 幂等保护：提案已执行/执行中则跳过（防止重复签名触发二次执行）
        if proposal.status in ("executed", "executing"):
            await db.commit()
            return SignResult(
                current_signatures=proposal.current_signatures,
                threshold=proposal.threshold,
                auto_executed=True,
                execution_tx_hash=proposal.execution_tx_hash,
            )

        if proposal.type == "collection":
            # ─── 归集提案：触发后台归集执行 ─────────────
            try:
                # 找到关联的 Collection
                collection = (await db.execute(
                    select(Collection).where(Collection.proposal_id == proposal_id)
                )).scalar_one_or_none()

                if not collection:
                    raise RuntimeError("关联归集批次不存在")

                # 更新状态
                collection.status = "executing"
                proposal.status = "executed"
                proposal.executed_at = datetime.now(timezone.utc)
                auto_executed = True

                db.add(AuditLog(
                    admin_id=current_user.id,
                    admin_username=current_user.username,
                    action="execute_proposal",
                    detail=f"归集提案 #{proposal_id} 审批通过，开始执行归集 #{collection.id}",
                    ip_address=request.client.host if request.client else None,
                ))

                # 先提交，确保后台任务能读取到数据
                await db.commit()

                # 启动后台归集任务
                asyncio.create_task(execute_collection(collection.id))

            except Exception as e:
                logger.error("归集提案 #%d 执行失败: %s", proposal_id, e)
                proposal.status = "failed"
                # 同步取消归集
                if collection:
                    collection.status = "cancelled"
                db.add(AuditLog(
                    admin_id=current_user.id,
                    admin_username=current_user.username,
                    action="execute_proposal_failed",
                    detail=f"归集提案 #{proposal_id} 执行失败: {e}",
                    ip_address=request.client.host if request.client else None,
                ))

        elif proposal.type == "payout_batch":
            # ─── 批量打款提案 ──────────────────────────────
            payout = None
            try:
                payout = (await db.execute(
                    select(Payout).where(Payout.proposal_id == proposal_id)
                )).scalar_one_or_none()
                if not payout:
                    raise RuntimeError("关联打款批次不存在")

                if proposal.chain == "BSC" and wallet.is_multisig:
                    # BSC 多签：与归集流程相同 — 先 commit，再后台广播
                    all_sigs_result = await db.execute(
                        select(Signature).where(Signature.proposal_id == proposal_id)
                    )
                    all_sigs = all_sigs_result.scalars().all()
                    sig_pairs = [(s.signer_address, s.signature) for s in all_sigs]

                    payout.status = "executing"
                    proposal.status = "executing"
                    auto_executed = True

                    db.add(AuditLog(
                        admin_id=current_user.id,
                        admin_username=current_user.username,
                        action="execute_proposal",
                        detail=f"打款提案 #{proposal_id} 审批通过，开始后台广播 MultiSend，批次 #{payout.id}",
                        ip_address=request.client.host if request.client else None,
                    ))

                    await db.commit()
                    asyncio.create_task(
                        _execute_bsc_multisend_bg(proposal_id, payout.id, wallet.address, tx_data, sig_pairs)
                    )

                elif proposal.chain == "TRON" and wallet.is_multisig:
                    # TRON 多签：先快速校验，然后 commit 立即返回，后台执行广播+打款
                    relay_wallet_id = tx_data.get("_relay_wallet_id")
                    relay_wallet_address = tx_data.get("_relay_wallet_address")
                    if not relay_wallet_id or not relay_wallet_address:
                        raise RuntimeError("tx_data 缺少中转钱包信息")

                    relay_wallet = (await db.execute(
                        select(Wallet).where(Wallet.id == relay_wallet_id)
                    )).scalar_one_or_none()
                    if not relay_wallet or relay_wallet.derive_index is None:
                        raise RuntimeError(f"中转钱包 #{relay_wallet_id} 不存在或无 derive_index")

                    # 收集已有签名列表（在 commit 前读取，确保包含刚添加的签名）
                    all_sigs_result = await db.execute(
                        select(Signature).where(Signature.proposal_id == proposal_id)
                    )
                    all_sigs = all_sigs_result.scalars().all()
                    signatures = [s.signature for s in all_sigs]

                    payout.status = "executing"
                    proposal.status = "executing"
                    auto_executed = True

                    db.add(AuditLog(
                        admin_id=current_user.id,
                        admin_username=current_user.username,
                        action="execute_proposal",
                        detail=f"TRON 打款提案 #{proposal_id} 签名完成，后台执行多签广播+打款 #{payout.id}",
                        ip_address=request.client.host if request.client else None,
                    ))

                    await db.commit()
                    asyncio.create_task(
                        _execute_tron_payout_bg(
                            proposal_id, payout.id, wallet.address,
                            relay_wallet_id, tx_data, signatures,
                        )
                    )

                else:
                    # 普通钱包（有 derive_index）：后台逐笔执行
                    payout.status = "executing"
                    proposal.status = "executed"
                    proposal.executed_at = datetime.now(timezone.utc)
                    auto_executed = True

                    db.add(AuditLog(
                        admin_id=current_user.id,
                        admin_username=current_user.username,
                        action="execute_proposal",
                        detail=f"打款提案 #{proposal_id} 审批通过，开始执行打款批次 #{payout.id}",
                        ip_address=request.client.host if request.client else None,
                    ))

                    await db.commit()
                    asyncio.create_task(execute_payout(payout.id))

            except Exception as e:
                logger.error("打款提案 #%d 执行失败: %s", proposal_id, e)
                execution_error = str(e)
                proposal.status = "failed"
                if payout:
                    payout.status = "failed"
                db.add(AuditLog(
                    admin_id=current_user.id,
                    admin_username=current_user.username,
                    action="execute_proposal_failed",
                    detail=f"打款提案 #{proposal_id} 执行失败: {e}",
                    ip_address=request.client.host if request.client else None,
                ))

        else:
            # ─── Safe 转账提案：链上多签执行 ─────────────
            try:
                all_sigs_result = await db.execute(
                    select(Signature).where(Signature.proposal_id == proposal_id)
                )
                all_sigs = all_sigs_result.scalars().all()

                if proposal.chain == "BSC":
                    gas_wallet = (await db.execute(
                        select(Wallet).where(
                            Wallet.chain == "BSC",
                            Wallet.type == "gas",
                        )
                    )).scalars().first()

                    if not gas_wallet or gas_wallet.derive_index is None:
                        raise RuntimeError("无可用 BSC Gas 钱包")

                    gas_key = get_private_key("BSC", gas_wallet.derive_index)

                    sig_pairs = [(s.signer_address, s.signature) for s in all_sigs]
                    execution_tx_hash = await proposal_service.execute_bsc_safe_tx(
                        wallet.address, tx_data, sig_pairs, gas_key,
                    )
                elif tx_data.get("_contract_multisig"):
                    # TRON 合约多签：立即标记 executing，再后台执行
                    proposal.status = "executing"
                    sig_pairs = [(s.signer_address, s.signature) for s in all_sigs]
                    await db.commit()
                    asyncio.create_task(
                        _execute_tron_contract_bg(proposal_id, wallet.address, tx_data, sig_pairs)
                    )
                    return SignResult(
                        success=True,
                        current_signatures=proposal.current_signatures,
                        threshold=proposal.threshold,
                        auto_executed=False,
                    )
                else:
                    # TRON 原生多签转账：立即标记 executing 防止被手动取消，再后台广播
                    proposal.status = "executing"
                    sig_list = [s.signature for s in all_sigs]
                    await db.commit()  # 先落库签名+状态，再起后台任务
                    asyncio.create_task(
                        _execute_tron_transfer_bg(proposal_id, wallet.address, tx_data, sig_list)
                    )
                    # 后台执行，直接返回
                    return SignResult(
                        success=True,
                        current_signatures=proposal.current_signatures,
                        threshold=proposal.threshold,
                        auto_executed=False,
                    )

                # BSC 同步执行完成，更新状态
                proposal.status = "executed"
                proposal.execution_tx_hash = execution_tx_hash
                proposal.executed_at = datetime.now(timezone.utc)
                auto_executed = True

                db.add(AuditLog(
                    admin_id=current_user.id,
                    admin_username=current_user.username,
                    action="execute_proposal",
                    detail=f"提案 #{proposal_id} 自动执行成功: {execution_tx_hash}",
                    ip_address=request.client.host if request.client else None,
                ))

                # TG 通知
                from app.core.telegram import notifier
                _p_token = getattr(proposal, "token", None) or "USDT"
                _token_label = ("BNB" if proposal.chain == "BSC" else "TRX") if _p_token == "native" else "USDT"
                asyncio.create_task(notifier.notify_proposal_executed(
                    chain=proposal.chain,
                    proposal_type=proposal.type,
                    title=proposal.title or "",
                    amount=tx_data.get("_amount", ""),
                    to_address=tx_data.get("_to_address", ""),
                    tx_hash=execution_tx_hash or "",
                    token=_token_label,
                ))

            except Exception as e:
                logger.error("提案 #%d 自动执行失败: %s", proposal_id, e)
                execution_error = str(e)
                proposal.status = "failed"
                db.add(AuditLog(
                    admin_id=current_user.id,
                    admin_username=current_user.username,
                    action="execute_proposal_failed",
                    detail=f"提案 #{proposal_id} 执行失败: {e}",
                    ip_address=request.client.host if request.client else None,
                ))

    # 归集提案在 threshold 分支内已 commit，其他情况需要 flush
    if not (proposal.type == "collection" and auto_executed):
        await db.flush()

    return SignResult(
        success=True,
        current_signatures=proposal.current_signatures,
        threshold=proposal.threshold,
        auto_executed=auto_executed,
        execution_tx_hash=execution_tx_hash,
        execution_error=execution_error,
    )


# ─── TRON 转账后台执行（避免前端 HTTP 超时）──────────

async def _execute_tron_transfer_bg(
    proposal_id: int,
    wallet_address: str,
    tx_data: dict,
    sig_list: list[str],
) -> None:
    """后台异步执行 TRON 多签转账（含能量租赁 + 轮询等待 + 广播）"""
    from app.models.system_settings import SystemSettings as SS

    # ── 调试开关：True = 只模拟能量，把结果写入 description，不租能量不广播 ──
    _ENERGY_DEBUG = False

    logger.info("提案 #%d 开始后台执行 TRON 转账 (debug=%s)", proposal_id, _ENERGY_DEBUG)
    execution_tx_hash: str | None = None
    execution_error: str | None = None

    try:
        async with AsyncSessionLocal() as db:
            ss = (await db.execute(select(SS).where(SS.id == 1))).scalar_one_or_none()

            # 租赁能量
            token_type = tx_data.get("_token", "usdt")
            if token_type == "usdt" and ss and ss.tron_energy_rental_enabled:
                try:
                    to_address = tx_data.get("_to_address", "")
                    amount_sun = int(tx_data.get("_amount_sun", "1000000"))
                    estimated_energy = await estimate_transfer_energy(
                        ss.tron_api_urls or [], ss.tron_api_keys or [],
                        wallet_address, to_address, amount_sun,
                        ss.tron_usdt_contract or "",
                    )
                    logger.info("提案 #%d 能量估算: %d, to=%s", proposal_id, estimated_energy, to_address[:10])

                    if _ENERGY_DEBUG:
                        raise RuntimeError(f"[调试断点] 估算={estimated_energy}，to={to_address}")

                    energy_result = await tron_energy_service.ensure_energy(
                        api_urls=ss.tron_api_urls,
                        api_keys=ss.tron_api_keys,
                        address=wallet_address,
                        energy_needed=estimated_energy,
                        rental_enabled=True,
                        rental_api_url=ss.tron_energy_rental_api_url or "",
                        rental_api_key=ss.tron_energy_rental_api_key or "",
                        rental_max_price_sun=ss.tron_energy_rental_max_price or 420,
                        rental_duration_ms=ss.tron_energy_rental_duration or 3_600_000,
                    )
                    if energy_result.get("rented"):
                        logger.info("提案 #%d 能量租赁成功: %s", proposal_id, energy_result.get("rental_tx"))
                        needed_energy = energy_result.get("needed", 85_000)
                        for _ in range(10):  # 最多等 30 秒
                            await asyncio.sleep(3)
                            res = await tron_energy_service.get_account_resource(
                                ss.tron_api_urls, ss.tron_api_keys, wallet_address,
                            )
                            avail = tron_energy_service.get_available_energy(res)
                            logger.info("提案 #%d 等待能量到账: %d/%d", proposal_id, avail, needed_energy)
                            if avail >= needed_energy:
                                logger.info("提案 #%d 能量已到账，等待节点同步", proposal_id)
                                await asyncio.sleep(6)  # 让委托状态同步到所有节点
                                break
                        else:
                            logger.warning("提案 #%d 能量等待超时(30s)，继续广播", proposal_id)
                    elif energy_result.get("error"):
                        logger.warning("提案 #%d 能量租赁失败(不阻断): %s", proposal_id, energy_result["error"])
                except Exception as e:
                    logger.warning("提案 #%d 能量租赁异常(不阻断): %s", proposal_id, e)

            # 广播前补充 TRX（带宽费，避免 BANDWITH_ERROR）
            try:
                async with AsyncSessionLocal() as _db_gas:
                    _gas_w = (await _db_gas.execute(
                        select(Wallet).where(Wallet.chain == "TRON", Wallet.type == "gas")
                    )).scalars().first()
                if _gas_w and _gas_w.derive_index is not None:
                    _multisig_trx = await chain_client.get_native_balance("TRON", wallet_address)
                    if _multisig_trx < Decimal("2"):
                        _deficit = Decimal("2") - _multisig_trx
                        _gas_key = get_private_key("TRON", _gas_w.derive_index)
                        await chain_client.send_native("TRON", _gas_key, _gas_w.address, wallet_address, _deficit)
                        logger.info("提案 #%d 补充多签钱包 TRX: %s → %s", proposal_id, _deficit, wallet_address[:10])
                        await asyncio.sleep(3)
            except Exception as _be:
                logger.warning("提案 #%d 补 TRX 失败（继续广播）: %s", proposal_id, _be)

            # 广播
            execution_tx_hash = await proposal_service.execute_tron_multisig_tx(tx_data, sig_list)
            logger.info("提案 #%d 广播成功: %s", proposal_id, execution_tx_hash)

    except Exception as e:
        logger.error("提案 #%d 后台执行失败: %s", proposal_id, e)
        execution_error = str(e)

    # 更新 DB 状态
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Proposal).where(Proposal.id == proposal_id))
            proposal = result.scalar_one_or_none()
            if proposal:
                if execution_tx_hash:
                    proposal.status = "executed"
                    proposal.execution_tx_hash = execution_tx_hash
                    proposal.executed_at = datetime.now(timezone.utc)
                    db.add(AuditLog(
                        admin_id=proposal.created_by,
                        admin_username="system",
                        action="execute_proposal",
                        detail=f"提案 #{proposal_id} 后台执行成功: {execution_tx_hash}",
                    ))
                    await db.commit()
                    # TG 通知
                    from app.core.telegram import notifier
                    _td = tx_data or {}
                    _p_token2 = getattr(proposal, "token", None) or "USDT"
                    _token_label2 = ("BNB" if proposal.chain == "BSC" else "TRX") if _p_token2 == "native" else "USDT"
                    asyncio.create_task(notifier.notify_proposal_executed(
                        chain=proposal.chain,
                        proposal_type=proposal.type,
                        title=proposal.title or "",
                        amount=_td.get("_amount", ""),
                        to_address=_td.get("_to_address", ""),
                        tx_hash=execution_tx_hash,
                        token=_token_label2,
                    ))
                else:
                    proposal.status = "failed"
                    db.add(AuditLog(
                        admin_id=proposal.created_by,
                        admin_username="system",
                        action="execute_proposal_failed",
                        detail=f"提案 #{proposal_id} 后台执行失败: {execution_error}",
                    ))
                    await db.commit()
    except Exception as e:
        logger.error("提案 #%d 更新状态失败: %s", proposal_id, e)


# ─── POST /{id}/reject — 拒绝/取消提案 ─────────────

@router.post("/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageMultisig],
):
    """拒绝/取消提案"""

    proposal = (await db.execute(
        select(Proposal).where(Proposal.id == proposal_id)
    )).scalar_one_or_none()

    if not proposal:
        raise HTTPException(status_code=404, detail="提案不存在")

    if proposal.status in ("executed", "rejected", "failed", "expired"):
        raise HTTPException(status_code=400, detail=f"提案已终结: {proposal.status}")
    if proposal.status == "executing":
        raise HTTPException(status_code=400, detail="提案正在执行中，无法取消")

    # 只有创建者或超级管理员可以取消
    if proposal.created_by != current_user.id and current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="只有创建者或超级管理员可以取消提案")

    proposal.status = "rejected"

    # 同步取消关联的归集任务
    if proposal.type == "collection":
        linked = (await db.execute(
            select(Collection).where(
                Collection.proposal_id == proposal_id,
                Collection.status.in_(["pending", "signing"]),
            )
        )).scalars().all()
        for col in linked:
            col.status = "cancelled"

    # 同步取消关联的打款批次
    if proposal.type == "payout_batch":
        linked_payouts = (await db.execute(
            select(Payout).where(
                Payout.proposal_id == proposal_id,
                Payout.status.in_(["pending", "signing"]),
            )
        )).scalars().all()
        for p in linked_payouts:
            p.status = "cancelled"

    await db.flush()

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="reject_proposal",
        detail=f"取消提案 #{proposal_id}",
        ip_address=request.client.host if request.client else None,
    ))

    await db.commit()

    from app.core.telegram import notifier
    asyncio.create_task(notifier.notify_proposal_cancelled(
        chain=proposal.chain or "",
        proposal_type=proposal.type or "",
        title=proposal.title or "",
        operator_name=current_user.username,
    ))

    return {"message": "提案已取消"}


# ─── BSC MultiSend 后台执行（避免前端 HTTP 超时）──────────

async def _execute_bsc_multisend_bg(
    proposal_id: int,
    payout_id: int,
    safe_address: str,
    tx_data: dict,
    sig_pairs: list[tuple[str, str]],
) -> None:
    """后台异步广播 BSC Safe MultiSend，完成后更新 DB 状态"""
    from app.core.hdwallet import get_private_key as _get_pk

    logger.info("提案 #%d BSC MultiSend 开始后台广播", proposal_id)
    execution_tx_hash: str | None = None
    execution_error: str | None = None

    try:
        async with AsyncSessionLocal() as db:
            gas_wallet = (await db.execute(
                select(Wallet).where(Wallet.chain == "BSC", Wallet.type == "gas")
            )).scalars().first()
            if not gas_wallet or gas_wallet.derive_index is None:
                raise RuntimeError("无可用 BSC Gas 钱包")
            gas_key = _get_pk("BSC", gas_wallet.derive_index)

        execution_tx_hash = await proposal_service.execute_bsc_safe_tx(
            safe_address, tx_data, sig_pairs, gas_key,
        )
        logger.info("提案 #%d BSC MultiSend 广播成功: %s", proposal_id, execution_tx_hash)

    except Exception as e:
        logger.error("提案 #%d BSC MultiSend 广播失败: %s", proposal_id, e)
        execution_error = str(e)

    # 更新 DB
    try:
        async with AsyncSessionLocal() as db:
            proposal = (await db.execute(
                select(Proposal).where(Proposal.id == proposal_id)
            )).scalar_one_or_none()
            payout = (await db.execute(
                select(Payout).where(Payout.id == payout_id)
            )).scalar_one_or_none()

            if execution_tx_hash:
                if proposal:
                    proposal.status = "executed"
                    proposal.execution_tx_hash = execution_tx_hash
                    proposal.executed_at = datetime.now(timezone.utc)
                if payout:
                    payout.status = "completed"
                    payout.executed_at = datetime.now(timezone.utc)
                    items = (await db.execute(
                        select(PayoutItem).where(PayoutItem.payout_id == payout_id)
                    )).scalars().all()
                    for it in items:
                        it.status = "completed"
                        it.tx_hash = execution_tx_hash
                db.add(AuditLog(
                    admin_id=proposal.created_by if proposal else 1,
                    admin_username="system",
                    action="execute_proposal",
                    detail=f"提案 #{proposal_id} BSC MultiSend 广播成功: {execution_tx_hash}，批次 #{payout_id}",
                ))
                await db.commit()

                from app.core.telegram import notifier
                asyncio.create_task(notifier.notify_proposal_executed(
                    chain="BSC",
                    proposal_type="payout_batch",
                    title=proposal.title if proposal else "",
                    amount=str(tx_data.get("_total_amount", "")),
                    to_address=f"{tx_data.get('_item_count', '?')} 笔 MultiSend",
                    tx_hash=execution_tx_hash,
                    token="USDT",
                ))
            else:
                if proposal:
                    proposal.status = "failed"
                if payout:
                    payout.status = "failed"
                db.add(AuditLog(
                    admin_id=proposal.created_by if proposal else 1,
                    admin_username="system",
                    action="execute_proposal_failed",
                    detail=f"提案 #{proposal_id} BSC MultiSend 广播失败: {execution_error}",
                ))
                await db.commit()
    except Exception as e:
        logger.error("提案 #%d 后台更新 DB 状态失败: %s", proposal_id, e)


# ─── TRON 打款多签 后台执行（避免前端 HTTP 超时）────────

async def _execute_tron_payout_bg(
    proposal_id: int,
    payout_id: int,
    multisig_address: str,
    relay_wallet_id: int,
    tx_data: dict,
    signatures: list[str],
) -> None:
    """后台异步执行 TRON 打款多签广播：补 TRX → 广播 → 切换 wallet_id → execute_payout"""
    logger.info("提案 #%d TRON 打款开始后台广播", proposal_id)
    transfer_tx_hash: str | None = None
    execution_error: str | None = None

    try:
        # 加载配置（能量租赁参数）
        _settings = await chain_client._load_settings()
        _api_urls = _settings.tron_api_urls or []
        _api_keys = _settings.tron_api_keys or []

        # 0. 如果是纯审批提案（中转余额已足够，创建时无需链上转账），直接跳过广播
        if tx_data.get("_no_transfer"):
            transfer_tx_hash = "RELAY_BALANCE_SUFFICIENT"
            logger.info("提案 #%d 为纯审批（_no_transfer），跳过链上广播直接触发打款", proposal_id)

        # 0b. 检查中转钱包是否已有足够余额，若是则跳过多签广播（运行时二次确认）
        _relay_addr = tx_data.get("_relay_wallet_address", "")
        _total_amount = Decimal(str(tx_data.get("_total_amount", "0")))
        if _relay_addr and _total_amount > 0:
            try:
                _relay_balance = await chain_client.get_usdt_balance("TRON", _relay_addr)
                if _relay_balance >= _total_amount:
                    logger.info(
                        "提案 #%d 中转钱包余额充足 (%s >= %s)，跳过多签广播，直接触发打款",
                        proposal_id, _relay_balance, _total_amount,
                    )
                    transfer_tx_hash = "RELAY_BALANCE_SUFFICIENT"  # 标记为余额充足跳过广播
            except Exception as _ce:
                logger.warning("提案 #%d 查询中转钱包余额失败，继续正常广播: %s", proposal_id, _ce)

        if transfer_tx_hash == "RELAY_BALANCE_SUFFICIENT":
            # 直接更新 DB 并触发打款，不走后续广播流程
            async with AsyncSessionLocal() as _db:
                _proposal = (await _db.execute(select(Proposal).where(Proposal.id == proposal_id))).scalar_one_or_none()
                _payout = (await _db.execute(select(Payout).where(Payout.id == payout_id))).scalar_one_or_none()
                if _proposal:
                    _proposal.status = "executed"
                    _proposal.execution_tx_hash = None
                    _proposal.executed_at = datetime.now(timezone.utc)
                if _payout:
                    _payout.wallet_id = relay_wallet_id
                    _payout.status = "executing"
                _db.add(AuditLog(
                    admin_id=_proposal.created_by if _proposal else 1,
                    admin_username="system",
                    action="execute_proposal",
                    detail=f"提案 #{proposal_id} 中转钱包余额充足，跳过多签广播，直接触发打款 #{payout_id}",
                ))
                await _db.commit()
            asyncio.create_task(execute_payout(payout_id))
            return

        # 1. 广播前给多签钱包补充 TRX（带宽费）
        async with AsyncSessionLocal() as _db:
            _gas_w = (await _db.execute(
                select(Wallet).where(Wallet.chain == "TRON", Wallet.type == "gas")
            )).scalars().first()
        if _gas_w and _gas_w.derive_index is not None:
            try:
                _trx = await chain_client.get_native_balance("TRON", multisig_address)
                if _trx < Decimal("2"):
                    _key = get_private_key("TRON", _gas_w.derive_index)
                    await chain_client.send_native("TRON", _key, _gas_w.address, multisig_address, Decimal("2") - _trx)
                    logger.info("提案 #%d 补充多签钱包 TRX: %s → %s", proposal_id, Decimal("2") - _trx, multisig_address[:10])
                    await asyncio.sleep(3)
            except Exception as _e:
                logger.warning("提案 #%d 补 TRX 失败（继续广播）: %s", proposal_id, _e)

        _is_contract_multisig = bool(tx_data.get("_contract_multisig"))

        # 2. 租赁能量
        # 合约多签：gas 钱包执行 execute()，租给 gas 钱包，固定 200,000
        # 原生多签：多签钱包自己广播，租给多签钱包，模拟估算
        if _settings.tron_energy_rental_enabled and _settings.tron_energy_rental_api_url:
            try:
                if _is_contract_multisig:
                    _energy_addr = _gas_w.address if _gas_w else multisig_address
                    _energy_needed = 200_000
                else:
                    _relay_addr = tx_data.get("_relay_wallet_address", "")
                    _total_amount_sun = int(Decimal(str(tx_data.get("_total_amount", "0"))) * 1_000_000)
                    _energy_needed = await estimate_transfer_energy(
                        _api_urls, _api_keys,
                        multisig_address, _relay_addr, _total_amount_sun,
                        _settings.tron_usdt_contract or "",
                    )
                    _energy_addr = multisig_address
                _energy_result = await tron_energy_service.ensure_energy(
                    _api_urls, _api_keys, _energy_addr,
                    energy_needed=_energy_needed,
                    rental_enabled=True,
                    rental_api_url=(_settings.tron_energy_rental_api_url or "").strip(),
                    rental_api_key=(_settings.tron_energy_rental_api_key or "").strip(),
                    rental_max_price_sun=_settings.tron_energy_rental_max_price or 420,
                    rental_duration_ms=_settings.tron_energy_rental_duration or 3_600_000,
                )
                if _energy_result.get("rented"):
                    logger.info("提案 #%d 能量租赁成功，等待生效", proposal_id)
                    await asyncio.sleep(3)
                elif _energy_result.get("sufficient"):
                    logger.info("提案 #%d 能量已充足", proposal_id)
                else:
                    logger.warning("提案 #%d 能量租赁失败（继续广播，将消耗 TRX）: %s",
                                   proposal_id, _energy_result.get("error"))
            except Exception as _ee:
                logger.warning("提案 #%d 能量租赁异常（继续广播）: %s", proposal_id, _ee)
        else:
            logger.info("提案 #%d 未启用能量租赁，广播时将消耗 TRX 燃烧支付 energy", proposal_id)

        # 3. 广播多签转账（multisig → relay wallet）
        if _is_contract_multisig:
            # 合约多签：需要 sig_pairs（含 signer_address），重新从 DB 获取
            if not _gas_w or _gas_w.derive_index is None:
                raise RuntimeError("无可用 TRON Gas 钱包，无法执行合约多签")
            async with AsyncSessionLocal() as _db_sig:
                _sigs_db = (await _db_sig.execute(
                    select(Signature).where(Signature.proposal_id == proposal_id)
                )).scalars().all()
                _sig_pairs = [(s.signer_address, s.signature) for s in _sigs_db]
            _gas_key = get_private_key("TRON", _gas_w.derive_index)
            transfer_tx_hash = await proposal_service.execute_tron_contract_tx(
                contract_address=tx_data["_contract_address"],
                token_address=tx_data.get("_token_address", _settings.tron_usdt_contract or ""),
                to_address=tx_data.get("_relay_wallet_address", ""),
                # 使用 _amount（签名时的转账金额=差额），而非 _total_amount（含中转已有余额）
                amount=Decimal(str(tx_data.get("_amount", tx_data.get("_total_amount", "0")))),
                nonce=tx_data["nonce"],
                signatures=_sig_pairs,
                gas_wallet_address=_gas_w.address,
                gas_wallet_private_key=_gas_key,
            )
        else:
            # 原生多签：直接广播预签名交易
            transfer_tx_hash = await proposal_service.execute_tron_multisig_tx(tx_data, signatures)
        logger.info("提案 #%d TRON 多签交易已广播: %s，等待链上确认", proposal_id, transfer_tx_hash)

        # 4. 链上确认：等待 TRON 出块（约 3s），验证 tx 是否真正成功
        await asyncio.sleep(5)
        import httpx as _httpx
        _confirmed = False
        for _attempt in range(10):  # 最多等 50s
            try:
                async with _httpx.AsyncClient(timeout=10) as _c:
                    _api_url = _api_urls[0] if _api_urls else "https://api.trongrid.io"
                    _headers = {"Content-Type": "application/json"}
                    if _api_keys:
                        _headers["TRON-PRO-API-KEY"] = _api_keys[0]
                    _r = await _c.post(
                        f"{_api_url.rstrip('/')}/wallet/gettransactioninfobyid",
                        json={"value": transfer_tx_hash},
                        headers=_headers,
                    )
                    if _r.status_code == 200:
                        _info = _r.json()
                        if _info:  # 空对象表示 tx 未确认
                            _receipt = _info.get("receipt", {})
                            _result = _receipt.get("result", "")
                            if _result == "SUCCESS":
                                _confirmed = True
                                logger.info("提案 #%d 链上确认成功 (attempt %d)", proposal_id, _attempt + 1)
                                break
                            elif _result in ("FAILED", "REVERT", "OUT_OF_ENERGY", "OUT_OF_TIME"):
                                raise RuntimeError(
                                    f"多签转账链上执行失败: {_result} "
                                    f"(energy_usage={_receipt.get('energy_usage_total', '?')}) txid={transfer_tx_hash}"
                                )
                            # result 为空或其他值：tx 已找到但结果未出，继续等待
                            logger.info("提案 #%d tx 已找到但结果未出 (result=%r)，继续等待", proposal_id, _result)
                            break
            except RuntimeError:
                raise
            except Exception as _ve:
                logger.warning("提案 #%d 查询链上结果失败 (attempt %d): %s", proposal_id, _attempt + 1, _ve)
            await asyncio.sleep(5)
        else:
            logger.warning("提案 #%d 链上确认超时（50s），假设成功继续", proposal_id)

        if not _confirmed:
            # 无法确认时仍继续，payout_executor 会在等待资金到账阶段发现余额为 0 并失败
            logger.warning("提案 #%d 未能确认链上成功，继续触发 execute_payout（将在余额等待阶段自动失败）", proposal_id)

    except Exception as e:
        logger.error("提案 #%d TRON 多签广播失败: %s", proposal_id, e)
        execution_error = str(e)

    # 如果广播成功但链上确认失败（RuntimeError from 验证），execution_error 已设置，
    # 此时 transfer_tx_hash 也有值，需要优先以 execution_error 为准
    if execution_error:
        transfer_tx_hash = None

    # 3. 更新 DB 状态
    try:
        async with AsyncSessionLocal() as db:
            proposal = (await db.execute(select(Proposal).where(Proposal.id == proposal_id))).scalar_one_or_none()
            payout = (await db.execute(select(Payout).where(Payout.id == payout_id))).scalar_one_or_none()

            if transfer_tx_hash:
                if proposal:
                    proposal.status = "executed"
                    proposal.execution_tx_hash = transfer_tx_hash
                    proposal.executed_at = datetime.now(timezone.utc)
                if payout:
                    payout.wallet_id = relay_wallet_id
                    payout.status = "executing"
                db.add(AuditLog(
                    admin_id=proposal.created_by if proposal else 1,
                    admin_username="system",
                    action="execute_proposal",
                    detail=f"TRON 打款提案 #{proposal_id} 多签广播成功: {transfer_tx_hash}，开始执行打款 #{payout_id}",
                ))
                await db.commit()
                asyncio.create_task(execute_payout(payout_id))
            else:
                if proposal:
                    proposal.status = "failed"
                if payout:
                    payout.status = "failed"
                    # 同步更新所有 pending/processing items，写入错误原因
                    fail_items = (await db.execute(
                        select(PayoutItem).where(
                            PayoutItem.payout_id == payout_id,
                            PayoutItem.status.in_(["pending", "processing"]),
                        )
                    )).scalars().all()
                    for _it in fail_items:
                        _it.status = "failed"
                        _it.error_message = f"多签广播失败: {execution_error}"
                db.add(AuditLog(
                    admin_id=proposal.created_by if proposal else 1,
                    admin_username="system",
                    action="execute_proposal_failed",
                    detail=f"TRON 打款提案 #{proposal_id} 多签广播失败: {execution_error}",
                ))
                await db.commit()
    except Exception as e:
        logger.error("提案 #%d 后台更新 DB 状态失败: %s", proposal_id, e)


# ─── TRON 合约多签后台执行 ──────────────────────────────

async def _execute_tron_contract_bg(
    proposal_id: int,
    contract_address: str,
    tx_data: dict,
    sig_pairs: list[tuple[str, str]],  # [(signer_address, signature_hex), ...]
) -> None:
    """后台异步执行 TRON 合约多签：租能量 → 调用 execute()"""
    from app.models.system_settings import SystemSettings as SS
    from decimal import Decimal as D

    logger.info("提案 #%d 开始后台执行 TRON 合约多签", proposal_id)

    try:
        async with AsyncSessionLocal() as db:
            ss = (await db.execute(select(SS).where(SS.id == 1))).scalar_one_or_none()
            if not ss:
                raise RuntimeError("系统配置未初始化")

            # 选 TRON gas 钱包
            gas_wallet = (await db.execute(
                select(Wallet).where(
                    Wallet.chain == "TRON",
                    Wallet.type == "gas",
                    Wallet.derive_index.isnot(None),
                )
            )).scalars().first()
            if not gas_wallet:
                raise RuntimeError("无可用 TRON Gas 钱包")

        gas_key = get_private_key("TRON", gas_wallet.derive_index)
        token_address = tx_data.get("_token_address", ss.tron_usdt_contract or "")
        to_address = tx_data["_to_address"]
        amount = D(tx_data["_amount"])
        nonce = tx_data["nonce"]

        # 租能量（合约 execute() 含内部 USDT transfer，实测约消耗 228,000 energy，留余量取 250,000）
        try:
            energy_result = await tron_energy_service.ensure_energy(
                api_urls=ss.tron_api_urls or [],
                api_keys=ss.tron_api_keys or [],
                address=gas_wallet.address,
                energy_needed=250_000,
                rental_enabled=bool(ss.tron_energy_rental_enabled),
                rental_api_url=ss.tron_energy_rental_api_url or "",
                rental_api_key=ss.tron_energy_rental_api_key or "",
                rental_max_price_sun=ss.tron_energy_rental_max_price or 420,
                rental_duration_ms=ss.tron_energy_rental_duration or 3_600_000,
            )
            if not energy_result.get("sufficient"):
                logger.warning("提案 #%d 合约 execute 能量不足: %s", proposal_id, energy_result.get("error"))
        except Exception as e:
            logger.warning("提案 #%d 能量租赁异常（继续执行）: %s", proposal_id, e)

        tx_hash = await proposal_service.execute_tron_contract_tx(
            contract_address=contract_address,
            token_address=token_address,
            to_address=to_address,
            amount=amount,
            nonce=nonce,
            signatures=sig_pairs,
            gas_wallet_address=gas_wallet.address,
            gas_wallet_private_key=gas_key,
        )

        # 等待链上确认
        import asyncio as _asyncio
        from app.services.chain_client import chain_client as _cc
        confirmed = False
        for _ in range(20):
            await _asyncio.sleep(3)
            try:
                receipt = await _cc._tron_get_tx_receipt(
                    ss.tron_api_urls or [], ss.tron_api_keys or [], tx_hash,
                )
                result = receipt.get("receipt", {}).get("result", "")
                if result in ("FAILED", "OUT_OF_ENERGY", "REVERT"):
                    raise RuntimeError(f"合约 execute 链上失败: receipt.result={result}")
                if receipt.get("id") or receipt.get("blockNumber"):
                    confirmed = True
                    break
            except RuntimeError:
                raise
            except Exception:
                pass

        async with AsyncSessionLocal() as db:
            proposal = (await db.execute(
                select(Proposal).where(Proposal.id == proposal_id)
            )).scalar_one_or_none()
            if proposal:
                proposal.status = "executed"
                proposal.execution_tx_hash = tx_hash
                proposal.executed_at = datetime.now(timezone.utc)
                db.add(AuditLog(
                    admin_id=proposal.created_by if proposal else 1,
                    admin_username="system",
                    action="execute_proposal",
                    detail=f"TRON 合约多签提案 #{proposal_id} 执行成功: {tx_hash} (confirmed={confirmed})",
                ))
                await db.commit()

        logger.info("提案 #%d TRON 合约多签执行完成: %s", proposal_id, tx_hash)

        from app.core.telegram import notifier
        asyncio.create_task(notifier.notify_proposal_executed(
            chain="TRON",
            proposal_type="transfer",
            title=f"提案 #{proposal_id}",
            amount=str(amount),
            to_address=to_address,
            tx_hash=tx_hash,
            token="USDT",
        ))

    except Exception as e:
        logger.error("提案 #%d TRON 合约多签执行失败: %s", proposal_id, e)
        try:
            async with AsyncSessionLocal() as db:
                proposal = (await db.execute(
                    select(Proposal).where(Proposal.id == proposal_id)
                )).scalar_one_or_none()
                if proposal:
                    proposal.status = "failed"
                    db.add(AuditLog(
                        admin_id=proposal.created_by if proposal else 1,
                        admin_username="system",
                        action="execute_proposal_failed",
                        detail=f"TRON 合约多签提案 #{proposal_id} 执行失败: {e}",
                    ))
                    await db.commit()
        except Exception as e2:
            logger.error("提案 #%d 更新失败状态出错: %s", proposal_id, e2)
