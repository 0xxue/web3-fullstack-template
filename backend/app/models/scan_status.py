from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from app.database import Base


class ScanStatus(Base):
    """扫描状态 - 记录每条链最后扫描的区块号"""
    __tablename__ = "scan_status"

    id = Column(Integer, primary_key=True)
    chain = Column(String(10), nullable=False, unique=True, index=True)  # BSC | TRON
    last_scanned_block = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
