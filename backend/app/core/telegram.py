"""
Telegram Bot 通知服务

支持两种通知渠道：
1. 群组通知 — 发送到管理群（system_settings.tg_admin_chat_id）
2. 私聊通知 — 发送 DM 给每个管理员（admins.tg_chat_id）

自动绑定：管理员在系统预填 TG 用户名，给 Bot 发 /start 后自动匹配并保存 chat_id。

使用方式：
    from app.core.telegram import notifier
    await notifier.notify_large_deposit(chain="BSC", address=addr, amount=amt, tx_hash=tx, db=db)
"""

import asyncio
import copy
import logging
import re
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.admin import Admin
from app.models.deposit_address import DepositAddress
from app.models.system_settings import SystemSettings
from app.core.notification_defaults import DEFAULT_NOTIFICATION_TEMPLATES

TYPE_TITLES = {
    "deposit": "新充值",
    "large_deposit": "⚠️ 大额充值",
    "proposal_created": "新多签提案",
    "proposal_signed": "提案签名更新",
    "proposal_executed": "提案已执行",
    "proposal_cancelled": "提案已取消",
    "collection_completed": "归集完成",
    "payout_batch_created": "批量打款创建",
    "payout_completed": "打款完成",
    "system_alert": "系统告警",
}

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class _SafeFormatDict(dict):
    """format_map 时缺失的 key 保留 {key} 原样，不报 KeyError。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class TelegramNotifier:

    def __init__(self):
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0
        # 待审批的群组: {super_admin_chat_id: {"group_id": str, "group_title": str}}
        self._pending_groups: dict[str, dict] = {}

    # ─── 内部方法 ───────────────────────────────────────

    async def _get_bot_token(self, db: AsyncSession) -> Optional[str]:
        result = await db.execute(
            select(SystemSettings.tg_bot_token).where(SystemSettings.id == 1)
        )
        return result.scalar_one_or_none()

    async def _get_group_chat_id(self, db: AsyncSession) -> Optional[str]:
        result = await db.execute(
            select(SystemSettings.tg_admin_chat_id).where(SystemSettings.id == 1)
        )
        return result.scalar_one_or_none()

    async def _get_admin_chat_ids(self, db: AsyncSession) -> list[str]:
        result = await db.execute(
            select(Admin.tg_chat_id).where(
                Admin.is_active == True,  # noqa: E712
                Admin.tg_chat_id.isnot(None),
                Admin.tg_chat_id != "",
            )
        )
        return [row[0] for row in result.all()]

    async def _send_message(self, token: str, chat_id: str, text: str) -> bool:
        """发送单条消息，永不抛异常。"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{TELEGRAM_API_BASE.format(token=token)}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.warning(
                        "Telegram 发送失败 chat_id=%s: %s",
                        chat_id,
                        data.get("description", "未知错误"),
                    )
                    return False
                return True
        except httpx.TimeoutException:
            logger.warning("Telegram 发送超时 chat_id=%s", chat_id)
            return False
        except Exception as e:
            logger.error("Telegram 发送异常 chat_id=%s: %s", chat_id, e)
            return False

    async def _send_to_group(self, db: AsyncSession, text: str) -> bool:
        token = await self._get_bot_token(db)
        chat_id = await self._get_group_chat_id(db)
        if not token or not chat_id:
            return False
        return await self._send_message(token, chat_id, text)

    async def _send_to_all_admins(self, db: AsyncSession, text: str) -> int:
        token = await self._get_bot_token(db)
        if not token:
            return 0
        chat_ids = await self._get_admin_chat_ids(db)
        success = 0
        for cid in chat_ids:
            if await self._send_message(token, cid, text):
                success += 1
        return success

    async def _notify(
        self,
        text: str,
        db: Optional[AsyncSession] = None,
        group: bool = True,
        dm: bool = False,
    ) -> None:
        """核心分发。传入 db 则复用，否则自建 session。"""
        if db is not None:
            if group:
                await self._send_to_group(db, text)
            if dm:
                await self._send_to_all_admins(db, text)
        else:
            async with AsyncSessionLocal() as session:
                if group:
                    await self._send_to_group(session, text)
                if dm:
                    await self._send_to_all_admins(session, text)

    # ─── getUpdates 轮询 + 自动绑定 ─────────────────────

    async def _poll_updates(self, token: str) -> list[dict]:
        """调用 getUpdates 获取新消息和事件。"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{TELEGRAM_API_BASE.format(token=token)}/getUpdates",
                    params={
                        "offset": self._last_update_id + 1,
                        "timeout": 5,
                        "allowed_updates": '["message","my_chat_member"]',
                    },
                )
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])
                return []
        except Exception as e:
            logger.debug("getUpdates 失败: %s", e)
            return []

    async def _handle_update(self, update: dict, token: str) -> None:
        """处理 update：私聊 /start 绑定管理员、群组加入审批、/approve 确认群组。"""
        # ── Bot 被加入群组事件 ──
        member_update = update.get("my_chat_member")
        if member_update:
            await self._handle_group_join(member_update, token)
            return

        message = update.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        chat_type = chat.get("type", "")
        chat_id = str(chat.get("id", ""))
        tg_user = message.get("from", {})
        tg_username = tg_user.get("username", "")

        # 群组里的 /bindgroup 命令 — 超管直接在群里绑定
        if chat_type in ("group", "supergroup") and text.startswith("/bindgroup"):
            await self._handle_bindgroup(token, chat_id, chat, tg_user)
            return

        # 只处理私聊命令
        if chat_type != "private":
            return

        if text.startswith("/start"):
            await self._handle_start(token, chat_id, tg_username, tg_user)
        elif text.startswith("/approve"):
            await self._handle_approve(token, chat_id, tg_username)

    async def _handle_group_join(self, member_update: dict, token: str) -> None:
        """Bot 被加入群组时，通知所有超管审批。"""
        new_status = member_update.get("new_chat_member", {}).get("status", "")
        old_status = member_update.get("old_chat_member", {}).get("status", "")

        # 只处理 Bot 从非 member 变为 member/administrator 的情况
        if new_status not in ("member", "administrator"):
            return
        if old_status in ("member", "administrator"):
            return

        chat = member_update.get("chat", {})
        group_id = str(chat.get("id", ""))
        group_title = chat.get("title", "未知群组")
        inviter = member_update.get("from", {})
        inviter_name = inviter.get("first_name", "") or inviter.get("username", "未知")

        logger.info("Bot 被加入群组: %s (%s)，邀请人: %s", group_title, group_id, inviter_name)

        # 查找所有超管，发送审批请求
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Admin).where(
                    Admin.role == "super_admin",
                    Admin.is_active == True,  # noqa: E712
                    Admin.tg_chat_id.isnot(None),
                    Admin.tg_chat_id != "",
                )
            )
            super_admins = result.scalars().all()

            if not super_admins:
                logger.warning("无法发送群组审批：没有已绑定 TG 的超级管理员")
                return

            bot_token = await self._get_bot_token(db)
            if not bot_token:
                return

            for sa in super_admins:
                # 记录待审批信息
                self._pending_groups[sa.tg_chat_id] = {
                    "group_id": group_id,
                    "group_title": group_title,
                }
                await self._send_message(
                    bot_token,
                    sa.tg_chat_id,
                    f"<b>📢 群组绑定申请</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"群组: <b>{group_title}</b>\n"
                    f"群组 ID: <code>{group_id}</code>\n"
                    f"邀请人: {inviter_name}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"回复 /approve 同意将此群组设为通知群组\n"
                    f"忽略此消息则不绑定",
                )

    async def _handle_start(
        self, token: str, chat_id: str, tg_username: str, tg_user: dict
    ) -> None:
        """处理私聊 /start —— 自动匹配并绑定管理员。"""
        if not tg_username:
            first_name = tg_user.get("first_name", "")
            await self._send_message(
                token,
                chat_id,
                f"您好 {first_name}，您未设置 Telegram 用户名，无法自动绑定。\n"
                f"请在 Telegram 设置中添加用户名后重试。",
            )
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Admin).where(
                    func.lower(Admin.tg_username) == tg_username.lower(),
                    Admin.is_active == True,  # noqa: E712
                )
            )
            admin_user = result.scalar_one_or_none()

            if admin_user is None:
                first_name = tg_user.get("first_name", "")
                await self._send_message(
                    token,
                    chat_id,
                    f"您好 {first_name}，您的 Telegram 用户名 <b>@{tg_username}</b> "
                    f"未在系统中注册。\n请联系管理员在后台添加您的 TG 用户名。",
                )
                logger.info("TG 用户 @%s 未匹配到管理员", tg_username)
                return

            if admin_user.tg_chat_id == chat_id:
                await self._send_message(
                    token,
                    chat_id,
                    f"✅ 您已绑定成功，无需重复操作。\n管理员: <b>{admin_user.username}</b>",
                )
                return

            admin_user.tg_chat_id = chat_id
            await db.commit()

            await self._send_message(
                token,
                chat_id,
                f"✅ <b>绑定成功！</b>\n\n"
                f"管理员: <b>{admin_user.username}</b>\n"
                f"TG 用户: @{tg_username}\n\n"
                f"您现在将自动收到系统通知。",
            )
            logger.info(
                "TG 自动绑定: @%s -> 管理员 %s (chat_id=%s)",
                tg_username,
                admin_user.username,
                chat_id,
            )

    async def _handle_approve(self, token: str, chat_id: str, tg_username: str) -> None:
        """处理 /approve —— 超管确认群组绑定。"""
        pending = self._pending_groups.pop(chat_id, None)
        if not pending:
            await self._send_message(
                token,
                chat_id,
                "当前没有待审批的群组绑定请求。",
            )
            return

        group_id = pending["group_id"]
        group_title = pending["group_title"]

        # 验证操作者是超管
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Admin).where(
                    Admin.tg_chat_id == chat_id,
                    Admin.role == "super_admin",
                    Admin.is_active == True,  # noqa: E712
                )
            )
            admin_user = result.scalar_one_or_none()
            if not admin_user:
                await self._send_message(token, chat_id, "权限不足，仅超级管理员可审批。")
                return

            # 更新 system_settings 的群组 ID
            result = await db.execute(
                select(SystemSettings).where(SystemSettings.id == 1)
            )
            settings = result.scalar_one_or_none()
            if settings:
                settings.tg_admin_chat_id = group_id
                await db.commit()

                await self._send_message(
                    token,
                    chat_id,
                    f"✅ <b>群组绑定成功！</b>\n\n"
                    f"群组: <b>{group_title}</b>\n"
                    f"ID: <code>{group_id}</code>\n\n"
                    f"系统通知将发送到该群组。",
                )
                # 清除其他超管的待审批记录
                to_remove = [
                    k for k, v in self._pending_groups.items()
                    if v.get("group_id") == group_id
                ]
                for k in to_remove:
                    self._pending_groups.pop(k, None)

                # 在群组里也发一条确认
                await self._send_message(
                    token,
                    group_id,
                    f"✅ 本群已绑定为系统通知群组。\n审批人: <b>{admin_user.username}</b>",
                )
                logger.info(
                    "群组绑定成功: %s (%s)，审批人: %s",
                    group_title,
                    group_id,
                    admin_user.username,
                )

    async def _handle_bindgroup(
        self, token: str, group_chat_id: str, chat: dict, tg_user: dict
    ) -> None:
        """在群组里发 /bindgroup，超管直接绑定该群为通知群组。"""
        tg_username = tg_user.get("username", "")
        group_title = chat.get("title", "未知群组")

        if not tg_username:
            await self._send_message(token, group_chat_id, "无法识别您的 Telegram 用户名。")
            return

        async with AsyncSessionLocal() as db:
            # 验证发送者是超管或操作员
            result = await db.execute(
                select(Admin).where(
                    func.lower(Admin.tg_username) == tg_username.lower(),
                    Admin.role.in_(["super_admin", "operator"]),
                    Admin.is_active == True,  # noqa: E712
                )
            )
            admin_user = result.scalar_one_or_none()

            if not admin_user:
                await self._send_message(
                    token, group_chat_id, "权限不足，仅超级管理员或操作员可执行此操作。"
                )
                return

            # 更新 system_settings
            result = await db.execute(
                select(SystemSettings).where(SystemSettings.id == 1)
            )
            settings = result.scalar_one_or_none()
            if settings:
                settings.tg_admin_chat_id = group_chat_id
                await db.commit()

                await self._send_message(
                    token,
                    group_chat_id,
                    f"✅ <b>本群已绑定为系统通知群组</b>\n\n"
                    f"群组: <b>{group_title}</b>\n"
                    f"操作人: <b>{admin_user.username}</b>\n\n"
                    f"系统通知将发送到本群。",
                )
                logger.info(
                    "群组绑定成功(bindgroup): %s (%s)，操作人: %s",
                    group_title,
                    group_chat_id,
                    admin_user.username,
                )

    async def start_polling(self) -> None:
        """启动后台轮询任务。使用文件锁确保多 worker 场景下只有一个进程轮询。"""
        if self._polling_task is not None:
            return

        import fcntl, os
        lock_path = "/tmp/vaultsign_tg_poll.lock"
        try:
            self._lock_fd = open(lock_path, "w")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
        except (BlockingIOError, OSError):
            # 另一个 worker 已持有锁，跳过
            logger.info("Telegram Bot 轮询已由其他 Worker 接管，当前 Worker 跳过")
            self._lock_fd = None
            return

        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info("Telegram Bot 轮询已启动 (PID=%d)", os.getpid())

    async def stop_polling(self) -> None:
        """停止后台轮询。"""
        if self._polling_task is not None:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
            logger.info("Telegram Bot 轮询已停止")
        # 释放文件锁
        if getattr(self, "_lock_fd", None) is not None:
            import fcntl
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            except Exception:
                pass
            self._lock_fd = None

    async def _polling_loop(self) -> None:
        """轮询主循环，每 3 秒检查一次新消息。"""
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    token = await self._get_bot_token(db)

                if not token:
                    # 没配置 token，等一会儿再查
                    await asyncio.sleep(30)
                    continue

                updates = await self._poll_updates(token)
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id > self._last_update_id:
                        self._last_update_id = update_id
                    await self._handle_update(update, token)

                await asyncio.sleep(3)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Telegram 轮询异常: %s", e)
                await asyncio.sleep(10)

    # ─── 模板渲染引擎 ──────────────────────────────────

    async def _get_template_config(
        self, notification_type: str, db: AsyncSession
    ) -> Optional[dict]:
        """从 DB 加载模板配置，合并默认值。返回 None 表示该通知已禁用。"""
        defaults = DEFAULT_NOTIFICATION_TEMPLATES.get(notification_type)
        if not defaults:
            return None

        config = copy.deepcopy(defaults)

        # 从 DB 加载自定义配置
        result = await db.execute(
            select(SystemSettings.notification_templates).where(SystemSettings.id == 1)
        )
        custom_all = result.scalar_one_or_none()
        if custom_all and isinstance(custom_all, dict):
            custom = custom_all.get(notification_type)
            if custom and isinstance(custom, dict):
                for key in ("enabled", "template", "group", "dm"):
                    if key in custom:
                        config[key] = custom[key]

        if not config.get("enabled", True):
            return None

        return config

    async def _save_notification(self, notification_type: str, variables: dict, body_text: str) -> None:
        """保存通知到数据库（异步，不阻塞主流程）。"""
        from app.models.notification import Notification
        try:
            # Strip HTML tags and decorative separator lines
            plain_body = re.sub(r'<[^>]+>', '', body_text).strip()
            plain_body = re.sub(r'[━─]{3,}', '', plain_body)
            plain_body = re.sub(r'\n{3,}', '\n\n', plain_body).strip()
            title = TYPE_TITLES.get(notification_type, notification_type)
            chain = variables.get("chain")
            # Extract relevant extra_data fields
            extra_keys = ("tx_hash", "amount", "token", "address", "to_address", "from_address")
            extra_data = {k: variables[k] for k in extra_keys if k in variables}

            async with AsyncSessionLocal() as session:
                notification = Notification(
                    type=notification_type,
                    chain=chain,
                    title=title,
                    body=plain_body,
                    extra_data=extra_data if extra_data else None,
                    is_read=False,
                )
                session.add(notification)
                await session.commit()
        except Exception as e:
            logger.error("保存通知到数据库失败: %s", e)

    async def _notify_with_template(
        self,
        notification_type: str,
        variables: dict,
        db: Optional[AsyncSession] = None,
    ) -> None:
        """渲染模板 + 根据配置分发通知。"""
        async def _do(session: AsyncSession) -> None:
            config = await self._get_template_config(notification_type, session)
            if config is None:
                return
            # SafeFormatDict: 缺失变量保留 {key} 原样
            safe_vars = _SafeFormatDict(variables)
            text = config["template"].format_map(safe_vars)
            group = config.get("group", True)
            dm = config.get("dm", False)
            if group:
                await self._send_to_group(session, text)
            if dm:
                await self._send_to_all_admins(session, text)
            # Save to DB notification (non-blocking)
            asyncio.create_task(self._save_notification(notification_type, variables, text))

        if db is not None:
            await _do(db)
        else:
            async with AsyncSessionLocal() as session:
                await _do(session)

    # ─── 区块浏览器 URL ─────────────────────────────────

    @staticmethod
    def _explorer_address_url(chain: str, address: str) -> str:
        if chain.upper() == "TRON":
            return f"https://tronscan.org/#/address/{address}"
        return f"https://bscscan.com/address/{address}"

    @staticmethod
    def _explorer_tx_url(chain: str, tx_hash: str) -> str:
        if chain.upper() == "TRON":
            return f"https://tronscan.org/#/transaction/{tx_hash}"
        return f"https://bscscan.com/tx/{tx_hash}"

    @staticmethod
    def _format_amount(amount: Decimal) -> str:
        """格式化金额：大额保留 2 位小数，小额保留有效位数（最多 8 位）。"""
        if amount >= 1:
            return f"{amount:,.2f}"
        # 小额：去除末尾零，最多 8 位小数
        return f"{amount:.8f}".rstrip("0").rstrip(".")

    def _build_deposit_vars(self, chain: str, address: str, amount: Decimal,
                            tx_hash: str, from_address: str,
                            token: str = "USDT",
                            address_label: str = "") -> dict:
        """构建充值通知的模板变量（完整地址 + 浏览器链接）。"""
        addr_url = self._explorer_address_url(chain, address)
        from_url = self._explorer_address_url(chain, from_address) if from_address else ""
        tx_url = self._explorer_tx_url(chain, tx_hash)
        # 地址显示：有备注则 "备注 (地址)"，否则仅地址
        address_display = f"{address_label} ({address})" if address_label else address
        return {
            "chain": chain,
            "token": token,
            "amount": self._format_amount(amount),
            "address": address,
            "address_display": address_display,
            "address_label": address_label or "",
            "address_url": addr_url,
            "from_address": from_address or "未知",
            "from_address_url": from_url,
            "tx_hash": tx_hash,
            "tx_url": tx_url,
        }

    # ─── 充值通知（每笔）──────────────────────────────

    async def _get_deposit_address_label(
        self, address: str, db: Optional[AsyncSession] = None,
    ) -> str:
        """查询充值地址的备注标签。"""
        async def _query(session: AsyncSession) -> str:
            result = await session.execute(
                select(DepositAddress.label).where(DepositAddress.address == address)
            )
            return result.scalar_one_or_none() or ""

        if db:
            return await _query(db)
        async with AsyncSessionLocal() as session:
            return await _query(session)

    async def notify_deposit(
        self,
        chain: str,
        address: str,
        amount: Decimal,
        tx_hash: str,
        from_address: str = "",
        token: str = "USDT",
        db: Optional[AsyncSession] = None,
    ) -> None:
        label = await self._get_deposit_address_label(address, db)
        variables = self._build_deposit_vars(chain, address, amount, tx_hash, from_address, token, label)
        await self._notify_with_template("deposit", variables, db=db)

    # ─── 大额充值 ──────────────────────────────────────

    async def notify_large_deposit(
        self,
        chain: str,
        address: str,
        amount: Decimal,
        tx_hash: str,
        from_address: str = "",
        token: str = "USDT",
        db: Optional[AsyncSession] = None,
    ) -> None:
        label = await self._get_deposit_address_label(address, db)
        variables = self._build_deposit_vars(chain, address, amount, tx_hash, from_address, token, label)
        await self._notify_with_template("large_deposit", variables, db=db)

    # ─── 新多签提案 ────────────────────────────────────

    async def notify_proposal_created(
        self,
        chain: str,
        proposal_type: str,
        title: str,
        threshold: int,
        creator_name: str,
        db: Optional[AsyncSession] = None,
    ) -> None:
        type_label = {"collection": "归集", "transfer": "转账", "payout": "打款"}.get(
            proposal_type, proposal_type
        )
        await self._notify_with_template("proposal_created", {
            "type_label": type_label,
            "chain": chain,
            "title": title,
            "threshold": str(threshold),
            "creator_name": creator_name,
        }, db=db)

    # ─── 提案签名进度 ──────────────────────────────────

    async def notify_proposal_signed(
        self,
        chain: str,
        title: str,
        signer_name: str,
        current_signatures: int,
        threshold: int,
        db: Optional[AsyncSession] = None,
    ) -> None:
        await self._notify_with_template("proposal_signed", {
            "chain": chain,
            "title": title,
            "signer_name": signer_name,
            "current_signatures": str(current_signatures),
            "threshold": str(threshold),
        }, db=db)

    # ─── 提案已执行 ────────────────────────────────────

    async def notify_proposal_executed(
        self,
        chain: str,
        proposal_type: str,
        title: str,
        amount: str = "",
        to_address: str = "",
        tx_hash: str = "",
        token: str = "USDT",
        db: Optional[AsyncSession] = None,
    ) -> None:
        type_label = {"collection": "归集", "transfer": "转账", "payout": "打款"}.get(
            proposal_type, proposal_type
        )
        await self._notify_with_template("proposal_executed", {
            "type_label": type_label,
            "chain": chain,
            "title": title,
            "amount": amount,
            "token": token,
            "to_address": to_address,
            "tx_hash": tx_hash,
        }, db=db)

    # ─── 提案已取消 ────────────────────────────────────

    async def notify_proposal_cancelled(
        self,
        chain: str,
        proposal_type: str,
        title: str,
        operator_name: str = "",
        db: Optional[AsyncSession] = None,
    ) -> None:
        type_label = {"collection": "归集", "transfer": "转账", "payout": "打款"}.get(
            proposal_type, proposal_type
        )
        await self._notify_with_template("proposal_cancelled", {
            "type_label": type_label,
            "chain": chain,
            "title": title,
            "operator_name": operator_name,
        }, db=db)

    # ─── 归集完成 ──────────────────────────────────────

    async def notify_collection_completed(
        self,
        chain: str,
        total_amount: Decimal,
        address_count: int,
        extra_text: str = "",
        db: Optional[AsyncSession] = None,
    ) -> None:
        await self._notify_with_template("collection_completed", {
            "chain": chain,
            "address_count": str(address_count),
            "total_amount": f"{total_amount:,.2f}",
            "extra": extra_text,
        }, db=db)

    # ─── 打款完成 ──────────────────────────────────────

    async def notify_payout_completed(
        self,
        chain: str,
        to_address: str,
        amount: Decimal,
        tx_hash: str = "",
        memo: str = "",
        db: Optional[AsyncSession] = None,
    ) -> None:
        addr_url = self._explorer_address_url(chain, to_address)
        tx_url = self._explorer_tx_url(chain, tx_hash) if tx_hash else ""
        memo_line = f"备注: {memo}\n" if memo else ""
        await self._notify_with_template("payout_completed", {
            "chain": chain,
            "to_address": to_address,
            "to_address_url": addr_url,
            "amount": f"{amount:,.2f}",
            "memo_line": memo_line,
            "tx_hash": tx_hash or "待确认",
            "tx_url": tx_url,
        }, db=db)

    async def notify_payout_batch_created(
        self,
        chain: str,
        wallet_address: str,
        item_count: int,
        total_amount: Decimal,
        memo: str = "",
        db: Optional[AsyncSession] = None,
    ) -> None:
        memo_line = f"备注: {memo}\n" if memo else ""
        await self._notify_with_template("payout_batch_created", {
            "chain": chain,
            "wallet_address": wallet_address,
            "item_count": item_count,
            "total_amount": f"{total_amount:,.2f}",
            "memo_line": memo_line,
        }, db=db)

    # ─── 系统告警 ──────────────────────────────────────

    async def notify_system_alert(
        self,
        level: str,
        title: str,
        detail: str = "",
        db: Optional[AsyncSession] = None,
    ) -> None:
        level_icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(level, "⚪")
        detail_line = f"\n详情: {detail}" if detail else ""
        await self._notify_with_template("system_alert", {
            "level_icon": level_icon,
            "level": level.upper(),
            "title": title,
            "detail_line": detail_line,
        }, db=db)


# ─── 模块级单例 ─────────────────────────────────────────

notifier = TelegramNotifier()
