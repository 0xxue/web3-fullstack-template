from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class DepositOut(BaseModel):
    id: int
    chain: str
    token: str = "USDT"
    address: str
    from_address: Optional[str]
    amount: Decimal
    tx_hash: str
    block_number: int
    confirmations: int
    status: str  # pending | confirming | confirmed
    created_at: datetime
    confirmed_at: Optional[datetime]

    class Config:
        from_attributes = True


class DepositListResponse(BaseModel):
    items: list[DepositOut]
    total: int
    page: int
    page_size: int


class TokenAmount(BaseModel):
    token: str
    amount: Decimal


class DepositStatsResponse(BaseModel):
    total_today: int
    amount_today: Decimal
    amount_by_token: list[TokenAmount] = []
    pending_count: int
    confirming_count: int
    confirmed_today: int
