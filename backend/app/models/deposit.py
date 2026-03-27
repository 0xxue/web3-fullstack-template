from sqlalchemy import Column, Integer, String, DateTime, Numeric, Index
from sqlalchemy.sql import func

from app.database import Base


class Deposit(Base):
    """充值记录"""
    __tablename__ = "deposits"

    id = Column(Integer, primary_key=True, index=True)
    chain = Column(String(10), nullable=False, index=True)          # BSC | TRON
    token = Column(String(10), nullable=False, default="USDT")     # USDT | BNB | TRX
    address = Column(String(128), nullable=False, index=True)       # 充值地址
    from_address = Column(String(128), nullable=True)               # 来源地址
    amount = Column(Numeric(36, 18), nullable=False)                # 充值金额(USDT)
    tx_hash = Column(String(128), nullable=False, unique=True)      # 交易哈希
    block_number = Column(Integer, nullable=False)                  # 区块号
    confirmations = Column(Integer, nullable=False, default=0)      # 确认数
    status = Column(String(20), nullable=False, default="pending")  # pending | confirming | confirmed
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('ix_deposits_chain_status', 'chain', 'status'),
        Index('ix_deposits_created_at', 'created_at'),
    )
