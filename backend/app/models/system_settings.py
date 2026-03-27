from sqlalchemy import Column, Integer, String, Boolean, DateTime, Numeric, JSON, func

from app.database import Base


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, default=1)
    # 登录安全
    require_2fa = Column(Boolean, default=False, nullable=False)
    enable_google_login = Column(Boolean, default=False, nullable=False)
    google_client_id = Column(String(200), nullable=True)
    # 会话
    session_timeout_minutes = Column(Integer, default=30, nullable=False)
    # Telegram Bot
    tg_bot_token = Column(String(200), nullable=True)
    tg_admin_chat_id = Column(String(100), nullable=True)
    # 归集阈值
    collection_min_bsc = Column(Numeric(36, 18), default=50, nullable=False)
    collection_min_tron = Column(Numeric(36, 18), default=10, nullable=False)
    # 大额充值通知阈值
    large_deposit_threshold = Column(Numeric(36, 18), default=10000, nullable=False)
    # 区块确认数
    bsc_confirmations = Column(Integer, default=15, nullable=False)
    tron_confirmations = Column(Integer, default=20, nullable=False)
    # 充值扫描间隔(秒)
    deposit_scan_interval = Column(Integer, default=15, nullable=False)
    # 原生代币充值监控 (BNB/TRX)
    native_token_monitoring = Column(Boolean, default=False, nullable=False)
    # 角色权限配置 (JSON)
    # {"operator": ["dashboard", ...], "signer": [...], "viewer": [...]}
    role_permissions = Column(JSON, nullable=True)
    # API / RPC 配置
    goldrush_api_keys = Column(JSON, default=[], nullable=False)
    bsc_rpc_urls = Column(JSON, default=[
        "https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed2.binance.org",
        "https://bsc-dataseed3.binance.org",
        "https://bsc-dataseed1.defibit.io",
    ], nullable=False)
    tron_api_urls = Column(JSON, default=[
        "https://api.trongrid.io",
        "https://api.tronstack.io",
        "https://rpc.ankr.com/tron",
    ], nullable=False)
    tron_api_keys = Column(JSON, default=[], nullable=False)
    bsc_usdt_contract = Column(String(128), default="0x55d398326f99059fF775485246999027B3197955", nullable=False)
    tron_usdt_contract = Column(String(128), default="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", nullable=False)
    # TRON 能量租赁
    tron_energy_rental_enabled = Column(Boolean, default=False, nullable=False)
    tron_energy_rental_api_url = Column(String(500), nullable=True)   # 第三方租赁平台 API 地址
    tron_energy_rental_api_key = Column(String(500), nullable=True)   # API Key
    tron_energy_rental_max_price = Column(Integer, default=420, nullable=False)  # 每单位能量最高价(sun)
    tron_energy_rental_duration = Column(Integer, default=3600000, nullable=False)  # 租赁时长(毫秒)
    # 通知模板自定义 (JSON)
    notification_templates = Column(JSON, nullable=True)
    # 时间
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
