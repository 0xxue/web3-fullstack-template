"""角色权限配置模块 — 定义功能模块常量和权限解析逻辑"""

# 所有可配置的功能模块
ALL_MODULES: list[str] = [
    # 页面模块（前 6 个）
    "dashboard",
    "deposits",
    "collections",
    "addresses",
    "payouts",
    "multisig",
    # 设置子模块
    "admin_manage",
    "system_params",
    "wallet_config",
    "telegram_config",
    "api_config",
    "audit_logs",
    # 通知权限（notif_ 前缀）
    "notif_deposit",
    "notif_large_deposit",
    "notif_collection_completed",
    "notif_proposal_created",
    "notif_proposal_signed",
    "notif_proposal_executed",
    "notif_proposal_cancelled",
    "notif_payout_batch_created",
    "notif_payout_completed",
    "notif_system_alert",
]

# 模块中文标签
MODULE_LABELS: dict[str, str] = {
    "dashboard": "首页总览",
    "deposits": "充值明细",
    "collections": "资金归集",
    "addresses": "地址库",
    "payouts": "打款汇出",
    "multisig": "多签管理",
    "admin_manage": "管理员管理",
    "system_params": "系统参数",
    "wallet_config": "钱包配置",
    "telegram_config": "Telegram配置",
    "api_config": "API配置",
    "audit_logs": "审计日志",
    "notif_deposit": "充值到账",
    "notif_large_deposit": "大额充值",
    "notif_collection_completed": "归集完成",
    "notif_proposal_created": "提案创建",
    "notif_proposal_signed": "提案签名",
    "notif_proposal_executed": "提案执行",
    "notif_proposal_cancelled": "提案取消",
    "notif_payout_batch_created": "打款创建",
    "notif_payout_completed": "打款完成",
    "notif_system_alert": "系统警告",
}

# 各角色默认权限（role_permissions 为 null 时使用）
DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "operator": [
        "dashboard", "deposits", "collections", "addresses", "payouts",
        "notif_deposit", "notif_large_deposit", "notif_collection_completed",
        "notif_payout_batch_created", "notif_payout_completed",
    ],
    "signer": [
        "dashboard", "multisig",
        "notif_proposal_created", "notif_proposal_signed", "notif_proposal_executed", "notif_proposal_cancelled",
    ],
    "viewer": ["dashboard", "deposits"],
}


def resolve_permissions(role: str, role_permissions_json: dict | None) -> list[str]:
    """计算某角色的有效权限列表

    - super_admin 始终返回全部模块
    - 其他角色优先使用数据库配置，回退到默认值
    """
    if role == "super_admin":
        return ALL_MODULES[:]

    if role_permissions_json and role in role_permissions_json:
        return role_permissions_json[role]

    return DEFAULT_PERMISSIONS.get(role, [])
