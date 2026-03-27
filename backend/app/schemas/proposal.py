"""
提案签名 — 请求/响应 Schema
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field


# ─── 创建提案 ────────────────────────────────────────

class ProposalCreate(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    type: str = Field(..., pattern="^(collection|transfer|payout)$")
    wallet_id: int
    title: str = Field(..., max_length=200)
    description: str | None = Field(None, max_length=2000)
    to_address: str = Field(..., max_length=128)
    amount: Decimal = Field(..., gt=0)
    token: str = Field("usdt", pattern="^(usdt|native)$")  # usdt 或 native(BNB/TRX)
    memo: str | None = Field(None, max_length=500)


# ─── 提交签名 ────────────────────────────────────────

class ProposalSign(BaseModel):
    signer_address: str = Field(..., max_length=128)
    signature: str
    # TronLink 手机版会刷新 expiration，需要前端把实际签名的 raw_data_hex 传回来
    signed_raw_data_hex: Optional[str] = None


# ─── 签名详情输出 ────────────────────────────────────

class SignatureOut(BaseModel):
    id: int
    signer_id: int
    signer_address: str
    signer_username: str | None = None
    signed_at: datetime

    class Config:
        from_attributes = True


# ─── 提案详情输出 ────────────────────────────────────

class ProposalOut(BaseModel):
    id: int
    chain: str
    type: str
    status: str
    title: str
    description: str | None = None
    wallet_id: int | None = None
    wallet_address: str | None = None
    to_address: str | None = None
    amount: str | None = None
    token: str | None = None        # usdt | native
    safe_tx_hash: str | None = None
    tx_data: dict | None = None
    threshold: int
    current_signatures: int
    owners: list[str] | None = None
    signatures: list[SignatureOut] = []
    created_by: int | None = None
    created_by_username: str | None = None
    execution_tx_hash: str | None = None
    executed_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── 提案列表响应 ────────────────────────────────────

class ProposalListResponse(BaseModel):
    items: list[ProposalOut]
    total: int
    page: int
    page_size: int


# ─── 签名结果 ────────────────────────────────────────

class SignResult(BaseModel):
    success: bool
    current_signatures: int
    threshold: int
    auto_executed: bool = False
    execution_tx_hash: str | None = None
    execution_error: str | None = None  # 执行失败时的具体错误信息
