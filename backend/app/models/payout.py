from sqlalchemy import Column, Integer, String, DateTime, Numeric, Text, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class Payout(Base):
    """打款批次"""
    __tablename__ = "payouts"

    id = Column(Integer, primary_key=True, index=True)
    chain = Column(String(10), nullable=False, index=True)              # BSC | TRON
    asset_type = Column(String(10), nullable=False, default="usdt")    # usdt | native
    status = Column(String(20), nullable=False, default="pending")      # pending | signing | executing | completed | partial | failed | cancelled
    total_amount = Column(Numeric(36, 18), nullable=False, default=0)   # 打款总金额
    item_count = Column(Integer, nullable=False, default=0)             # 笔数
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)  # 来源打款钱包
    memo = Column(Text, nullable=True)                                  # 批次备注
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True)  # 关联多签提案
    executed_at = Column(DateTime(timezone=True), nullable=True)        # 实际执行时间
    created_by = Column(Integer, ForeignKey("admins.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class PayoutItem(Base):
    """打款明细"""
    __tablename__ = "payout_items"

    id = Column(Integer, primary_key=True, index=True)
    payout_id = Column(Integer, ForeignKey("payouts.id"), nullable=False, index=True)
    to_address = Column(String(128), nullable=False)                    # 目标地址
    amount = Column(Numeric(36, 18), nullable=False)                    # 打款金额
    memo = Column(Text, nullable=True)                                  # 单笔备注
    tx_hash = Column(String(128), nullable=True)                        # 链上交易哈希（MultiSend 场景下多条 item 共享同一 tx_hash）
    status = Column(String(20), nullable=False, default="pending")      # pending | processing | completed | failed
    error_message = Column(String(500), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
