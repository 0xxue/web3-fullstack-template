from datetime import datetime

from pydantic import BaseModel, Field


class AddressOut(BaseModel):
    id: int
    chain: str
    derive_index: int
    address: str
    label: str | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AddressListResponse(BaseModel):
    items: list[AddressOut]
    total: int
    page: int
    page_size: int


class GenerateRequest(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    count: int = Field(1, ge=1, le=100)
    label: str | None = None


class GenerateResponse(BaseModel):
    generated: int
    addresses: list[AddressOut]


class UpdateLabelRequest(BaseModel):
    label: str | None = Field(None, max_length=200)


class AddressStatusResponse(BaseModel):
    mnemonic_configured: bool
    total_addresses: int
    bsc_count: int
    tron_count: int


class AddressBalanceResponse(BaseModel):
    native_symbol: str
    native_balance: str | None
    usdt_balance: str | None
