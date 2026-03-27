"""
Gas 钱包直接转账 API

Gas 钱包是普通 HD 钱包（非 Safe 多签），不走提案流程，
由系统直接使用私钥签名广播，立即返回 txHash。

POST /transfers/direct  —  Gas 钱包直接转账
"""

import json
import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.wallet import Wallet
from app.core.deps import require_module
from app.core.hdwallet import get_private_key
from app.services.chain_client import chain_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transfers", tags=["直接转账"])

CanTransfer = Depends(require_module("payouts"))


class DirectTransferRequest(BaseModel):
    chain: str = Field(..., description="BSC | TRON")
    wallet_id: int = Field(..., description="来源 Gas 钱包 ID")
    to_address: str = Field(..., description="目标地址")
    amount: Decimal = Field(..., gt=0, description="转账金额")
    token: str = Field("usdt", description="usdt | native")
    memo: str | None = None


class DirectTransferResult(BaseModel):
    tx_hash: str
    chain: str
    from_address: str
    to_address: str
    amount: Decimal
    token: str


@router.post("/direct", response_model=DirectTransferResult)
async def direct_transfer(
    body: DirectTransferRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanTransfer],
):
    """Gas 钱包直接转账（不需要多签）"""
    chain = body.chain.upper()

    # 1. 加载并校验来源钱包
    wallet = (await db.execute(
        select(Wallet).where(Wallet.id == body.wallet_id)
    )).scalar_one_or_none()

    if not wallet or not wallet.address:
        raise HTTPException(status_code=404, detail="钱包不存在")
    if wallet.type != "gas":
        raise HTTPException(status_code=400, detail="直接转账仅支持 Gas 钱包")
    if wallet.chain != chain:
        raise HTTPException(status_code=400, detail="钱包链不匹配")
    if wallet.derive_index is None:
        raise HTTPException(status_code=400, detail="Gas 钱包未配置 HD 索引")

    # 2. 余额检查
    try:
        if body.token == "usdt":
            balance = await chain_client.get_usdt_balance(chain, wallet.address)
            label = "USDT"
        else:
            balance = await chain_client.get_native_balance(chain, wallet.address)
            label = "BNB" if chain == "BSC" else "TRX"

        if balance < body.amount:
            raise HTTPException(
                status_code=400,
                detail=f"{label} 余额不足：当前 {float(balance):.6f}，需要 {float(body.amount):.6f}",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("余额查询失败，继续执行: %s", e)

    # 3. 获取私钥
    try:
        private_key = get_private_key(chain, wallet.derive_index)
    except Exception as e:
        logger.error("获取私钥失败: %s", e)
        raise HTTPException(status_code=500, detail="获取私钥失败")

    # 4. 广播交易
    try:
        if body.token == "usdt":
            tx_hash = await chain_client.send_usdt(
                chain, private_key, wallet.address, body.to_address, body.amount
            )
        else:
            tx_hash = await chain_client.send_native(
                chain, private_key, wallet.address, body.to_address, body.amount,
                wait_receipt=False,  # 广播后立即返回，不等链上确认
            )
    except Exception as e:
        logger.error("Gas 钱包转账失败: %s", e)
        raise HTTPException(status_code=500, detail=f"转账失败: {str(e)[:200]}")

    # 5. 审计日志（JSON 格式，方便历史查询解析）
    token_label = "USDT" if body.token == "usdt" else ("BNB" if chain == "BSC" else "TRX")
    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="direct_transfer",
        detail=json.dumps({
            "chain": chain,
            "token": body.token,
            "token_label": token_label,
            "from_address": wallet.address,
            "to_address": body.to_address,
            "amount": str(body.amount),
            "tx_hash": tx_hash,
            "memo": body.memo or "",
            "wallet_label": wallet.label or "",
        }, ensure_ascii=False),
        ip_address=request.client.host if request.client else None,
    ))
    await db.commit()

    logger.info(
        "Gas钱包转账成功: %s → %s %s %s, txHash=%s",
        wallet.address, body.to_address, body.amount, token_label, tx_hash,
    )

    return DirectTransferResult(
        tx_hash=tx_hash,
        chain=chain,
        from_address=wallet.address,
        to_address=body.to_address,
        amount=body.amount,
        token=body.token,
    )


@router.get("")
async def list_direct_transfers(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanTransfer],
    page_size: int = Query(50, ge=1, le=200),
):
    """查询最近的 Gas 钱包直接转账记录"""
    from app.models.audit_log import AuditLog as AuditLogModel
    rows = (await db.execute(
        select(AuditLogModel)
        .where(AuditLogModel.action == "direct_transfer")
        .order_by(AuditLogModel.id.desc())
        .limit(page_size)
    )).scalars().all()

    import re

    def _parse_detail(detail: str | None) -> dict:
        if not detail:
            return {}
        try:
            return json.loads(detail)
        except Exception:
            pass
        # 兼容旧明文格式：Gas钱包直接转账 {amount} {token} from {from} to {to} txHash={hash} chain={chain}
        d: dict = {}
        m = re.search(r'txHash=(\S+)', detail)
        if m:
            d["tx_hash"] = m.group(1)
        m = re.search(r'chain=(\S+)', detail)
        if m:
            d["chain"] = m.group(1)
        m = re.search(r'from\s+(\S+)', detail)
        if m:
            d["from_address"] = m.group(1)
        m = re.search(r'to\s+(\S+)', detail)
        if m:
            d["to_address"] = m.group(1)
        # amount + token: 第一个数字后紧跟 BNB/TRX/USDT
        m = re.search(r'([\d.]+)\s+(BNB|TRX|USDT)', detail)
        if m:
            d["amount"] = m.group(1)
            d["token_label"] = m.group(2)
            d["token"] = "native" if m.group(2) in ("BNB", "TRX") else "usdt"
        return d

    results = []
    for row in rows:
        d = _parse_detail(row.detail)
        results.append({
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "admin_username": row.admin_username,
            "chain": d.get("chain", ""),
            "token": d.get("token", ""),
            "token_label": d.get("token_label", ""),
            "from_address": d.get("from_address", ""),
            "to_address": d.get("to_address", ""),
            "amount": d.get("amount", ""),
            "tx_hash": d.get("tx_hash", ""),
            "memo": d.get("memo", ""),
            "wallet_label": d.get("wallet_label", ""),
        })
    return {"items": results, "total": len(results)}
