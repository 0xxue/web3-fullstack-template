from sqlalchemy import Column, Integer, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class DepositAddress(Base):
    __tablename__ = "deposit_addresses"

    id = Column(Integer, primary_key=True)
    chain = Column(String(10), nullable=False, index=True)
    derive_index = Column(Integer, nullable=False)
    address = Column(String(128), nullable=False, unique=True, index=True)
    label = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('chain', 'derive_index', name='uq_chain_index'),
    )
