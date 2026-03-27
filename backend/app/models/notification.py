from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, Text, Index
from sqlalchemy.sql import func
from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False)         # deposit, proposal_created, etc.
    chain = Column(String(10), nullable=True)          # BSC | TRON | None
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)                # plain text body (HTML stripped)
    extra_data = Column(JSON, nullable=True)           # {tx_hash, address, amount, ...}
    is_read = Column(Boolean, default=False, nullable=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index('ix_notification_is_read', 'is_read'),
        Index('ix_notification_created_at', 'created_at'),
    )
