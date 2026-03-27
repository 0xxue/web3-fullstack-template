from sqlalchemy import Column, Integer, String, Boolean, DateTime, func

from app.database import Base


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(20), nullable=False, default="viewer")
    signer_address_bsc = Column(String(42), nullable=True)
    signer_address_tron = Column(String(34), nullable=True)
    tg_username = Column(String(50), nullable=True)
    tg_chat_id = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    google_email = Column(String(100), nullable=True, unique=True)
    totp_secret = Column(String(32), nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    token_version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
