from sqlalchemy import Column, Integer, String, DateTime, Boolean, Index, JSON, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class Wallet(Base):
    """系统钱包配置：归集钱包 / 打款钱包 / Gas 钱包"""
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    chain = Column(String(10), nullable=False)                          # BSC | TRON
    type = Column(String(20), nullable=False)                           # collection | payout | gas
    address = Column(String(128), nullable=True)                        # 钱包地址（Safe 或 HD 派生）
    label = Column(String(100), nullable=True)                          # 标签
    derive_index = Column(Integer, nullable=True)                       # HD 派生索引（仅 gas / TRON 多签）

    # ─── 多签字段 ───
    is_multisig = Column(Boolean, default=False, nullable=False, server_default="false")
    owners = Column(JSON, nullable=True)                                # ["0xAddr1", ...] 签名人地址列表
    threshold = Column(Integer, nullable=True)                          # 需要几个签名
    deployment_tx = Column(String(128), nullable=True)                  # 部署/设置的交易哈希
    multisig_status = Column(String(20), nullable=True)                 # pending_fund | deploying | active | failed

    # ─── 中转钱包（TRON 多签打款专用）───
    relay_wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=True)  # 关联中转钱包 ID
    is_relay_wallet = Column(Boolean, default=False, nullable=False, server_default="false")  # 是否为中转钱包（打标后不依赖引用关系）

    is_active = Column(Boolean, default=True, nullable=False, server_default="true")  # 软删除标志

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index('ix_wallet_chain_type', 'chain', 'type'),
    )
