from pydantic import BaseModel
from datetime import datetime


class AuditLogOut(BaseModel):
    id: int
    admin_id: int | None
    admin_username: str
    action: str
    detail: str | None
    ip_address: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int
