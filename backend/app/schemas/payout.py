from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator
import re


# ─── 创建打款 ─────────────────────────────────────────

BSC_ADDR_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')
TRON_ADDR_RE = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')


class PayoutItemCreate(BaseModel):
    to_address: str = Field(..., min_length=10, max_length=128)
    amount: Decimal = Field(..., gt=0)
    memo: str | None = Field(None, max_length=200)


class PayoutCreate(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    asset_type: str = Field(default="usdt", pattern="^(usdt|native)$")
    wallet_id: int
    items: list[PayoutItemCreate] = Field(..., min_length=1, max_length=500)
    memo: str | None = Field(None, max_length=500)

    @field_validator('items')
    @classmethod
    def validate_addresses(cls, items, info):
        chain = info.data.get('chain', '')
        for item in items:
            addr = item.to_address.strip()
            if chain == 'BSC' and not BSC_ADDR_RE.match(addr):
                raise ValueError(f'无效的 BSC 地址: {addr}')
            if chain == 'TRON' and not TRON_ADDR_RE.match(addr):
                raise ValueError(f'无效的 TRON 地址: {addr}')
        return items


# ─── 余额预检（创建前展示给用户）─────────────────────

class PayoutPrecheck(BaseModel):
    chain: str
    wallet_id: int
    items: list[PayoutItemCreate] = Field(..., min_length=1)
    asset_type: str = "usdt"


class PayoutPrecheckResult(BaseModel):
    ok: bool
    total_amount: Decimal           # 需转出 USDT 总量
    usdt_balance: Decimal           # 当前 USDT 余额
    usdt_sufficient: bool
    estimated_gas_native: Decimal   # 预估 gas 费（BNB/TRX）
    native_balance: Decimal         # 当前 native 余额（TRON USDT 时为 Gas 钱包余额）
    native_sufficient: bool
    gas_auto_supplement: bool = False  # TRON USDT：TRX 由 Gas 钱包自动补充
    estimated_energy_cost_trx: Decimal | None = None  # TRON 能量费估算
    feee_balance_trx: Decimal | None = None           # feee.io 账户 TRX 余额
    feee_balance_sufficient: bool | None = None        # feee.io 余额是否足够
    estimated_feee_cost_trx: Decimal | None = None    # feee.io 预估租赁费用
    error: str | None = None


# ─── 详情 / 列表 ──────────────────────────────────────

class PayoutItemOut(BaseModel):
    id: int
    to_address: str
    amount: Decimal
    memo: str | None
    status: str
    tx_hash: str | None
    error_message: str | None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PayoutOut(BaseModel):
    id: int
    chain: str
    asset_type: str
    status: str
    total_amount: Decimal
    item_count: int
    wallet_id: int
    wallet_address: str | None = None
    memo: str | None
    proposal_id: int | None = None
    created_by: int
    created_by_username: str | None = None
    executed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    items: list[PayoutItemOut] | None = None

    class Config:
        from_attributes = True


class PayoutListResponse(BaseModel):
    items: list[PayoutOut]
    total: int
    page: int
    page_size: int
