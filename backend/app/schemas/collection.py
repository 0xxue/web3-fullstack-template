from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field


# ─── 扫描 ────────────────────────────────────────────

class ScanRequest(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    min_amount: Decimal = Field(default=Decimal("0"))
    asset_type: str = Field(default="usdt", pattern="^(usdt|native)$")


class ScannedAddressItem(BaseModel):
    address: str
    derive_index: int
    balance: Decimal
    native_balance: Decimal
    gas_needed: Decimal
    gas_sufficient: bool
    label: str | None = None


class ScanResponse(BaseModel):
    chain: str
    threshold: Decimal
    addresses: list[ScannedAddressItem]
    total_amount: Decimal
    count: int


# ─── 创建归集 ─────────────────────────────────────────

class CollectionAddressItem(BaseModel):
    address: str
    amount: Decimal


class CollectionWalletOption(BaseModel):
    id: int
    address: str
    label: str | None = None
    is_multisig: bool = False
    multisig_status: str | None = None


class CreateCollectionRequest(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    addresses: list[CollectionAddressItem] = Field(..., min_length=1)
    asset_type: str = Field(default="usdt", pattern="^(usdt|native)$")
    wallet_id: int | None = None  # 指定归集目标钱包（多个时选择用）


class CreateCollectionResponse(BaseModel):
    id: int
    chain: str
    status: str
    address_count: int
    total_amount: Decimal
    proposal_id: int | None = None
    created_at: datetime


# ─── 归集详情 / 列表 ──────────────────────────────────

class CollectionItemOut(BaseModel):
    id: int
    address: str
    amount: Decimal
    tx_hash: str | None
    gas_tx_hash: str | None = None
    status: str
    error_message: str | None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CollectionOut(BaseModel):
    id: int
    chain: str
    asset_type: str = "usdt"
    status: str
    total_amount: Decimal
    address_count: int
    target_address: str | None = None  # 归集目标钱包地址
    proposal_id: int | None = None
    created_by: int
    executed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    items: list[CollectionItemOut] | None = None

    class Config:
        from_attributes = True


class CollectionListResponse(BaseModel):
    items: list[CollectionOut]
    total: int
    page: int
    page_size: int
