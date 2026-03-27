from sqlalchemy import Column, Integer, String, DateTime, Numeric, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class Collection(Base):
    """归集批次"""
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, index=True)
    chain = Column(String(10), nullable=False, index=True)              # BSC | TRON
    asset_type = Column(String(10), nullable=False, default="usdt")    # usdt | native
    status = Column(String(20), nullable=False, default="pending")      # pending | signing | executing | completed | partial | failed | cancelled
    total_amount = Column(Numeric(36, 18), nullable=False, default=0)   # 归集总金额
    address_count = Column(Integer, nullable=False, default=0)          # 地址数
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True)  # 关联多签提案
    scheduled_at = Column(DateTime(timezone=True), nullable=True)       # 计划执行时间
    executed_at = Column(DateTime(timezone=True), nullable=True)        # 实际执行时间
    created_by = Column(Integer, ForeignKey("admins.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CollectionItem(Base):
    """归集明细"""
    __tablename__ = "collection_items"

    id = Column(Integer, primary_key=True, index=True)
    collection_id = Column(Integer, ForeignKey("collections.id"), nullable=False, index=True)
    address = Column(String(128), nullable=False)                       # 充值地址
    amount = Column(Numeric(36, 18), nullable=False)                    # 归集金额
    tx_hash = Column(String(128), nullable=True)                        # 转账交易哈希
    gas_tx_hash = Column(String(128), nullable=True)                    # 补 gas 交易哈希
    status = Column(String(20), nullable=False, default="pending")      # pending | gas_sent | transferring | completed | failed
    error_message = Column(String(500), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)            # 自动重试次数
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
