from pydantic import BaseModel, Field
from datetime import datetime


class AdminCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field(..., pattern="^(super_admin|operator|signer|viewer)$")
    signer_address_bsc: str | None = None
    signer_address_tron: str | None = None
    tg_username: str | None = None
    tg_chat_id: str | None = None
    google_email: str | None = None


class AdminUpdate(BaseModel):
    role: str | None = Field(None, pattern="^(super_admin|operator|signer|viewer)$")
    signer_address_bsc: str | None = None
    signer_address_tron: str | None = None
    tg_username: str | None = None
    tg_chat_id: str | None = None
    google_email: str | None = None
    is_active: bool | None = None


class AdminOut(BaseModel):
    id: int
    username: str
    role: str
    signer_address_bsc: str | None
    signer_address_tron: str | None
    tg_username: str | None
    tg_chat_id: str | None
    google_email: str | None
    is_active: bool
    totp_enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=128)


class AdminListResponse(BaseModel):
    items: list[AdminOut]
    total: int
    page: int
    page_size: int
