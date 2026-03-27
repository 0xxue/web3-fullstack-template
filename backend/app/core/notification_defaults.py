"""
通知模板默认值与元数据

8 种通知类型，每种包含：
- 默认模板（与原硬编码格式一致）
- 默认开关 + 群组/私聊渠道设置
- 可用变量列表（供前端展示）
"""

# ─── 默认模板 ─────────────────────────────────────────

DEFAULT_NOTIFICATION_TEMPLATES: dict[str, dict] = {
    "deposit": {
        "enabled": True,
        "group": True,
        "dm": False,
        "template": (
            "<b>📥 充值通知</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "链: <b>{chain}</b>\n"
            "金额: <b>{amount} {token}</b>\n"
            '充值地址: <a href="{address_url}">{address_display}</a>\n'
            '来源地址: <a href="{from_address_url}">{from_address}</a>\n'
            '交易哈希: <a href="{tx_url}">{tx_hash}</a>\n'
            "━━━━━━━━━━━━━━━"
        ),
    },
    "large_deposit": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>💰 大额充值通知</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "链: <b>{chain}</b>\n"
            "金额: <b>{amount} {token}</b>\n"
            '充值地址: <a href="{address_url}">{address_display}</a>\n'
            '来源地址: <a href="{from_address_url}">{from_address}</a>\n'
            '交易哈希: <a href="{tx_url}">{tx_hash}</a>\n'
            "━━━━━━━━━━━━━━━\n"
            "请及时关注并处理"
        ),
    },
    "proposal_created": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>📋 新多签提案</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "类型: <b>{type_label}</b>\n"
            "链: <b>{chain}</b>\n"
            "标题: {title}\n"
            "需要签名: <b>{threshold}</b> 个\n"
            "创建人: {creator_name}\n"
            "━━━━━━━━━━━━━━━\n"
            "请尽快登录系统进行签名"
        ),
    },
    "proposal_signed": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>✍️ 提案签名更新</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "链: <b>{chain}</b>\n"
            "标题: {title}\n"
            "签名人: {signer_name}\n"
            "进度: <b>{current_signatures}/{threshold}</b>\n"
            "━━━━━━━━━━━━━━━"
        ),
    },
    "proposal_executed": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>✅ 提案已执行</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "类型: <b>{type_label}</b>\n"
            "链: <b>{chain}</b>\n"
            "标题: {title}\n"
            "金额: <b>{amount} {token}</b>\n"
            "目标: <code>{to_address}</code>\n"
            "TxHash: <code>{tx_hash}</code>\n"
            "━━━━━━━━━━━━━━━\n"
            "多签提案已获得足够签名并成功执行"
        ),
    },
    "proposal_cancelled": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>❌ 提案已取消</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "类型: <b>{type_label}</b>\n"
            "链: <b>{chain}</b>\n"
            "标题: {title}\n"
            "操作人: {operator_name}\n"
            "━━━━━━━━━━━━━━━\n"
            "该提案已被取消"
        ),
    },
    "collection_completed": {
        "enabled": True,
        "group": True,
        "dm": False,
        "template": (
            "<b>📦 归集完成</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "链: <b>{chain}</b>\n"
            "归集地址数: <b>{address_count}</b>\n"
            "总金额: <b>{total_amount} USDT</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "资金已归集到归集钱包"
        ),
    },
    "payout_batch_created": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>📋 批量打款创建</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "链: <b>{chain}</b>\n"
            "打款钱包: <b>{wallet_address}</b>\n"
            "笔数: <b>{item_count} 笔</b>\n"
            "总金额: <b>{total_amount} USDT</b>\n"
            "{memo_line}"
            "━━━━━━━━━━━━━━━\n"
            "等待多签审批后执行"
        ),
    },
    "payout_completed": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>💸 打款完成</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "链: <b>{chain}</b>\n"
            '目标地址: <a href="{to_address_url}">{to_address}</a>\n'
            "金额: <b>{amount} USDT</b>\n"
            "{memo_line}"
            '交易哈希: <a href="{tx_url}">{tx_hash}</a>\n'
            "━━━━━━━━━━━━━━━\n"
            "打款已成功执行"
        ),
    },
    "system_alert": {
        "enabled": True,
        "group": True,
        "dm": True,
        "template": (
            "<b>{level_icon} 系统告警</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "级别: <b>{level}</b>\n"
            "内容: {title}{detail_line}\n"
            "━━━━━━━━━━━━━━━\n"
            "请及时检查系统状态"
        ),
    },
}

# ─── 通知类型元数据（label + 可用变量）─────────────────

NOTIFICATION_TYPE_META: dict[str, dict] = {
    "deposit": {
        "label": "充值通知",
        "variables": [
            {"name": "chain", "description": "链名称 (BSC / TRON)"},
            {"name": "token", "description": "代币类型 (USDT / BNB / TRX)"},
            {"name": "amount", "description": "充值金额"},
            {"name": "address", "description": "充值地址（完整）"},
            {"name": "address_display", "description": "充值地址（含备注）"},
            {"name": "address_label", "description": "充值地址备注"},
            {"name": "address_url", "description": "充值地址浏览器链接"},
            {"name": "from_address", "description": "来源地址（完整）"},
            {"name": "from_address_url", "description": "来源地址浏览器链接"},
            {"name": "tx_hash", "description": "交易哈希（完整）"},
            {"name": "tx_url", "description": "交易浏览器链接"},
        ],
    },
    "large_deposit": {
        "label": "大额充值通知",
        "variables": [
            {"name": "chain", "description": "链名称 (BSC / TRON)"},
            {"name": "token", "description": "代币类型 (USDT / BNB / TRX)"},
            {"name": "amount", "description": "充值金额"},
            {"name": "address", "description": "充值地址（完整）"},
            {"name": "address_display", "description": "充值地址（含备注）"},
            {"name": "address_label", "description": "充值地址备注"},
            {"name": "address_url", "description": "充值地址浏览器链接"},
            {"name": "from_address", "description": "来源地址（完整）"},
            {"name": "from_address_url", "description": "来源地址浏览器链接"},
            {"name": "tx_hash", "description": "交易哈希（完整）"},
            {"name": "tx_url", "description": "交易浏览器链接"},
        ],
    },
    "proposal_created": {
        "label": "新多签提案",
        "variables": [
            {"name": "type_label", "description": "提案类型 (归集/转账/打款)"},
            {"name": "chain", "description": "链名称"},
            {"name": "title", "description": "提案标题"},
            {"name": "threshold", "description": "所需签名数"},
            {"name": "creator_name", "description": "创建人"},
        ],
    },
    "proposal_signed": {
        "label": "提案签名更新",
        "variables": [
            {"name": "chain", "description": "链名称"},
            {"name": "title", "description": "提案标题"},
            {"name": "signer_name", "description": "签名人"},
            {"name": "current_signatures", "description": "当前签名数"},
            {"name": "threshold", "description": "所需签名数"},
        ],
    },
    "proposal_executed": {
        "label": "提案已执行",
        "variables": [
            {"name": "type_label", "description": "提案类型"},
            {"name": "chain", "description": "链名称"},
            {"name": "title", "description": "提案标题"},
            {"name": "amount", "description": "转账金额"},
            {"name": "token", "description": "代币类型 (USDT / BNB / TRX)"},
            {"name": "to_address", "description": "目标地址"},
            {"name": "tx_hash", "description": "交易哈希"},
        ],
    },
    "proposal_cancelled": {
        "label": "提案已取消",
        "variables": [
            {"name": "type_label", "description": "提案类型 (归集/转账/打款)"},
            {"name": "chain", "description": "链名称"},
            {"name": "title", "description": "提案标题"},
            {"name": "operator_name", "description": "操作人用户名"},
        ],
    },
    "collection_completed": {
        "label": "归集完成",
        "variables": [
            {"name": "chain", "description": "链名称"},
            {"name": "address_count", "description": "归集地址数"},
            {"name": "total_amount", "description": "总金额"},
        ],
    },
    "payout_batch_created": {
        "label": "批量打款创建",
        "variables": [
            {"name": "chain", "description": "链名称"},
            {"name": "wallet_address", "description": "打款钱包地址"},
            {"name": "item_count", "description": "打款笔数"},
            {"name": "total_amount", "description": "总金额"},
            {"name": "memo_line", "description": "备注行（含换行，为空时为空字符串）"},
        ],
    },
    "payout_completed": {
        "label": "打款完成",
        "variables": [
            {"name": "chain", "description": "链名称"},
            {"name": "to_address", "description": "目标地址（完整）"},
            {"name": "to_address_url", "description": "目标地址浏览器链接"},
            {"name": "amount", "description": "打款金额"},
            {"name": "memo_line", "description": "备注行（含换行，为空时为空字符串）"},
            {"name": "tx_hash", "description": "交易哈希（完整）"},
            {"name": "tx_url", "description": "交易浏览器链接"},
        ],
    },
    "system_alert": {
        "label": "系统告警",
        "variables": [
            {"name": "level_icon", "description": "级别图标 (🔴/🟡/🔵)"},
            {"name": "level", "description": "级别 (ERROR/WARNING/INFO)"},
            {"name": "title", "description": "告警内容"},
            {"name": "detail_line", "description": "详情行（含换行前缀，为空时为空字符串）"},
        ],
    },
}

# 所有通知类型 key 列表
NOTIFICATION_TYPES = list(DEFAULT_NOTIFICATION_TEMPLATES.keys())
