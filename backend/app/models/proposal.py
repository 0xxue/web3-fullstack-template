from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class Proposal(Base):
    """多签提案"""
    __tablename__ = "proposals"

    id = Column(Integer, primary_key=True, index=True)
    chain = Column(String(10), nullable=False, index=True)              # BSC | TRON
    type = Column(String(20), nullable=False, index=True)               # collection | transfer | payout
    status = Column(String(20), nullable=False, default="pending")      # pending | signing | executed | rejected | expired
    title = Column(String(200), nullable=False)                         # 提案标题
    description = Column(Text, nullable=True)                           # 详细描述
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=True)
    tx_data = Column(Text, nullable=True)                               # 链上交易数据(JSON)
    safe_tx_hash = Column(String(128), nullable=True)                   # Safe交易哈希 / TRON tx hash
    threshold = Column(Integer, nullable=False, default=2)              # 所需签名数
    current_signatures = Column(Integer, nullable=False, default=0)     # 当前签名数
    execution_tx_hash = Column(String(128), nullable=True)              # 链上执行交易哈希
    created_by = Column(Integer, ForeignKey("admins.id"), nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Signature(Base):
    """提案签名"""
    __tablename__ = "signatures"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=False, index=True)
    signer_id = Column(Integer, ForeignKey("admins.id"), nullable=False)
    signer_address = Column(String(128), nullable=False)                # 签名人钱包地址
    signature = Column(Text, nullable=False)                            # 签名数据
    signed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("proposal_id", "signer_address", name="uq_signature_proposal_signer"),
    )
