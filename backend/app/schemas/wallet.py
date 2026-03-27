from pydantic import BaseModel, Field
from datetime import datetime
from decimal import Decimal


class WalletOut(BaseModel):
    id: int
    chain: str
    type: str
    address: str | None = None
    label: str | None = None
    derive_index: int | None = None
    is_multisig: bool = False
    owners: list[str] | None = None
    threshold: int | None = None
    deployment_tx: str | None = None
    multisig_status: str | None = None
    relay_wallet_id: int | None = None
    is_relay_wallet: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WalletWithBalance(WalletOut):
    """钱包 + 链上余额"""
    native_balance: Decimal | None = None     # BNB / TRX
    usdt_balance: Decimal | None = None       # USDT


class WalletCreate(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    type: str = Field(..., pattern="^(collection|payout|gas)$")
    address: str | None = Field(None, max_length=128)
    label: str | None = Field(None, max_length=100)
    derive_index: int | None = Field(None, ge=0)


class WalletUpdate(BaseModel):
    address: str | None = Field(None, max_length=128)
    label: str | None = Field(None, max_length=100)
    relay_wallet_id: int | None = None
