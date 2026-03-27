from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # App
    APP_NAME: str = "多签钱包管理系统"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/multisig_wallet"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str = ""  # REQUIRED: set in .env
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # HD Wallet
    HD_MNEMONIC: str = ""                  # 明文助记词（兼容旧配置）
    HD_MNEMONIC_ENCRYPTED: str = ""        # AES-GCM 加密后的助记词（base64）
    HD_ENCRYPTION_KEY: str = ""            # 32字节加密密钥（hex）

    # Default super admin
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = ""  # REQUIRED: set in .env

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
