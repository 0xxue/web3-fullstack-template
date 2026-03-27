from decimal import Decimal
from pydantic import BaseModel
from datetime import datetime


class SystemSettingsOut(BaseModel):
    require_2fa: bool
    enable_google_login: bool
    google_client_id: str | None
    session_timeout_minutes: int
    # TG
    tg_bot_token: str | None
    tg_admin_chat_id: str | None
    # 归集阈值
    collection_min_bsc: Decimal
    collection_min_tron: Decimal
    # 大额充值通知阈值
    large_deposit_threshold: Decimal
    # 区块确认数
    bsc_confirmations: int
    tron_confirmations: int
    # 充值扫描间隔
    deposit_scan_interval: int
    # 原生代币监控
    native_token_monitoring: bool

    updated_at: datetime

    class Config:
        from_attributes = True


class SystemSettingsUpdate(BaseModel):
    require_2fa: bool | None = None
    enable_google_login: bool | None = None
    google_client_id: str | None = None
    session_timeout_minutes: int | None = None
    # TG
    tg_bot_token: str | None = None
    tg_admin_chat_id: str | None = None
    # 归集阈值
    collection_min_bsc: Decimal | None = None
    collection_min_tron: Decimal | None = None
    # 大额充值通知
    large_deposit_threshold: Decimal | None = None
    # 区块确认数
    bsc_confirmations: int | None = None
    tron_confirmations: int | None = None
    # 充值扫描间隔
    deposit_scan_interval: int | None = None
    # 原生代币监控
    native_token_monitoring: bool | None = None


class SystemSettingsPublic(BaseModel):
    """公开设置（登录页需要知道是否启用 Google 登录等）"""
    require_2fa: bool
    enable_google_login: bool
    google_client_id: str | None


class TelegramConfigOut(BaseModel):
    tg_bot_token: str | None
    tg_admin_chat_id: str | None

    class Config:
        from_attributes = True


class TelegramConfigUpdate(BaseModel):
    tg_bot_token: str | None = None
    tg_admin_chat_id: str | None = None


class TelegramTestRequest(BaseModel):
    chat_id: str


# ─── 角色权限 ─────────────────────────────────────


class ModuleInfo(BaseModel):
    key: str
    label: str


class RolePermissionsOut(BaseModel):
    all_modules: list[ModuleInfo]
    defaults: dict[str, list[str]]
    current: dict[str, list[str]]


class RolePermissionsUpdate(BaseModel):
    operator: list[str]
    signer: list[str]
    viewer: list[str]


# ─── API / RPC 配置 ──────────────────────────────


class ApiConfigOut(BaseModel):
    goldrush_api_keys: list[str]
    bsc_rpc_urls: list[str]
    tron_api_urls: list[str]
    tron_api_keys: list[str]
    bsc_usdt_contract: str
    tron_usdt_contract: str
    # TRON 能量租赁
    tron_energy_rental_enabled: bool
    tron_energy_rental_api_url: str | None
    tron_energy_rental_api_key: str | None
    tron_energy_rental_max_price: int
    tron_energy_rental_duration: int

    class Config:
        from_attributes = True


class ApiConfigUpdate(BaseModel):
    goldrush_api_keys: list[str] | None = None
    bsc_rpc_urls: list[str] | None = None
    tron_api_urls: list[str] | None = None
    tron_api_keys: list[str] | None = None
    bsc_usdt_contract: str | None = None
    tron_usdt_contract: str | None = None
    # TRON 能量租赁
    tron_energy_rental_enabled: bool | None = None
    tron_energy_rental_api_url: str | None = None
    tron_energy_rental_api_key: str | None = None
    tron_energy_rental_max_price: int | None = None
    tron_energy_rental_duration: int | None = None


# ─── 通知模板 ────────────────────────────────────


class NotificationVariableInfo(BaseModel):
    name: str
    description: str


class NotificationTypeInfo(BaseModel):
    key: str
    label: str
    variables: list[NotificationVariableInfo]
    enabled: bool
    template: str
    group: bool
    dm: bool
    threshold: str | None = None  # 仅 large_deposit 使用


class NotificationTemplatesOut(BaseModel):
    types: list[NotificationTypeInfo]


class NotificationTemplateUpdate(BaseModel):
    enabled: bool | None = None
    template: str | None = None
    group: bool | None = None
    dm: bool | None = None
    threshold: str | None = None  # 仅 large_deposit 使用


class NotificationTemplatesUpdate(BaseModel):
    templates: dict[str, NotificationTemplateUpdate]
