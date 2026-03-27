"""
充值监控扫描服务

后台服务，定期扫描 BSC 和 TRON 链上充值事件：
- USDT (BEP-20/TRC-20) Transfer 事件
- BNB 原生转账（BSC 区块内交易）
- TRX 原生转账（TronGrid 交易 API）

匹配系统充值地址后写入 Deposit 表，并跟踪确认数。

使用方式：
    from app.services.deposit_scanner import scanner
    await scanner.start()   # 启动
    await scanner.stop()    # 停止
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import base58
import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.deposit import Deposit
from app.models.deposit_address import DepositAddress
from app.models.scan_status import ScanStatus
from app.models.system_settings import SystemSettings
from app.models.wallet import Wallet
from app.core.telegram import notifier

logger = logging.getLogger(__name__)

# ERC-20 Transfer(address,address,uint256) event topic
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# 首次运行回溯区块数（约 5 分钟）
DEFAULT_LOOKBACK_BSC = 100
DEFAULT_LOOKBACK_TRON = 100  # ~5 分钟（TRON ~3秒/块）

# 最小充值金额（过滤粉尘攻击），低于此金额不记录
MIN_DEPOSIT_AMOUNT = Decimal("0.0001")


class DepositScanner:

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    # ─── 生命周期 ────────────────────────────────────────

    async def start(self):
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._scan_loop())
        logger.info("充值扫描服务已启动")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("充值扫描服务已停止")

    # ─── 主循环 ──────────────────────────────────────────

    async def _scan_loop(self):
        # 首次启动等待 5 秒，让其他服务先就绪
        await asyncio.sleep(5)

        while True:
            scan_interval = 15
            try:
                async with AsyncSessionLocal() as db:
                    settings = await self._load_settings(db)
                    if not settings:
                        await asyncio.sleep(30)
                        continue

                    scan_interval = settings.deposit_scan_interval or 15

                    # 加载所有活跃充值地址
                    addr_data = await self._load_deposit_addresses(db)
                    address_sets = addr_data["sets"]

                # ── 并行执行所有扫描（各用独立 db session）──
                tasks = []

                if settings.bsc_rpc_urls:
                    tasks.append(self._run_scan(
                        "BSC USDT", self._scan_bsc,
                        settings, address_sets.get("BSC", set()),
                    ))
                    if settings.native_token_monitoring:
                        tasks.append(self._run_scan(
                            "BSC BNB", self._scan_bsc_native,
                            settings, address_sets.get("BSC", set()),
                        ))

                if settings.tron_api_urls:
                    tasks.append(self._run_scan(
                        "TRON BLOCK", self._scan_tron_blocks,
                        settings, address_sets.get("TRON", set()),
                    ))

                # 确认数更新也并行
                tasks.append(self._run_confirmations(settings))

                await asyncio.gather(*tasks)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("充值扫描主循环异常: %s", e, exc_info=True)

            await asyncio.sleep(scan_interval)

    async def _run_scan(self, name: str, scan_fn, settings, addresses):
        """包装单个扫描任务：独立 db session + commit/rollback。"""
        try:
            async with AsyncSessionLocal() as db:
                await scan_fn(db, settings, addresses)
                await db.commit()
        except Exception as e:
            logger.error("%s 扫描异常: %s", name, e, exc_info=True)

    async def _run_confirmations(self, settings):
        """确认数更新任务。"""
        try:
            async with AsyncSessionLocal() as db:
                await self._update_confirmations(db, settings)
                await db.commit()
        except Exception as e:
            logger.error("确认数更新异常: %s", e, exc_info=True)

    # ─── 加载配置和地址 ──────────────────────────────────

    async def _load_settings(self, db: AsyncSession) -> Optional[SystemSettings]:
        result = await db.execute(
            select(SystemSettings).where(SystemSettings.id == 1)
        )
        return result.scalar_one_or_none()

    async def _load_deposit_addresses(self, db: AsyncSession) -> dict:
        """加载所有活跃充值地址，按链分组。
        BSC 地址转小写。TRON 保持 base58 原格式（按地址查询 TRC20）。
        """
        result = await db.execute(
            select(DepositAddress.chain, DepositAddress.address).where(
                DepositAddress.is_active == True  # noqa: E712
            )
        )
        sets: dict[str, set[str]] = {"BSC": set(), "TRON": set()}
        for chain, address in result.all():
            if chain == "BSC":
                sets["BSC"].add(address.lower())
            elif chain == "TRON":
                sets["TRON"].add(address)
        return {"sets": sets, "tron_map": {}}

    async def _load_system_wallet_addresses(self, db: AsyncSession, chain: str) -> set[str]:
        """加载系统钱包地址（gas/collection/payout）用于过滤内部转账。
        TRON 返回 base58 原格式；BSC 返回小写 hex。
        """
        result = await db.execute(
            select(Wallet.address).where(
                Wallet.chain == chain,
                Wallet.is_active == True,  # noqa: E712
                Wallet.address.isnot(None),
            )
        )
        addrs = {row[0] for row in result.all() if row[0]}
        if chain == "BSC":
            return {a.lower() for a in addrs}
        return addrs

    @staticmethod
    def _tron_base58_to_hex(base58_addr: str) -> Optional[str]:
        """将 TRON base58 地址 (T...) 转为 0x hex 格式。"""
        try:
            decoded = base58.b58decode_check(base58_addr)
            # 去掉 TRON 的 0x41 前缀，加 0x
            return "0x" + decoded[1:].hex()
        except Exception:
            return None

    @staticmethod
    def _tron_base58_to_hex41(base58_addr: str) -> Optional[str]:
        """将 TRON base58 地址 (T...) 转为 41-prefixed hex（区块数据格式）。"""
        try:
            decoded = base58.b58decode_check(base58_addr)
            return decoded.hex()
        except Exception:
            return None

    @staticmethod
    def _tron_hex41_to_base58(hex41_addr: str) -> Optional[str]:
        """将 41-prefixed hex 地址转为 TRON base58 地址 (T...)。"""
        try:
            addr_bytes = bytes.fromhex(hex41_addr)
            return base58.b58encode_check(addr_bytes).decode("ascii")
        except Exception:
            return None

    @staticmethod
    def _tron_hex_to_base58(hex_addr: str) -> Optional[str]:
        """将 0x hex 格式地址转为 TRON base58 地址 (T...)。"""
        try:
            # 去掉 0x 前缀，加上 TRON 主网前缀 41
            clean = hex_addr[2:] if hex_addr.startswith("0x") else hex_addr
            addr_bytes = bytes.fromhex("41" + clean)
            return base58.b58encode_check(addr_bytes).decode("ascii")
        except Exception:
            return None

    # ─── 扫描状态读写 ────────────────────────────────────

    async def _get_last_block(self, db: AsyncSession, chain: str) -> int:
        result = await db.execute(
            select(ScanStatus.last_scanned_block).where(ScanStatus.chain == chain)
        )
        return result.scalar_one_or_none() or 0

    async def _set_last_block(self, db: AsyncSession, chain: str, block: int):
        stmt = pg_insert(ScanStatus).values(
            chain=chain, last_scanned_block=block,
        ).on_conflict_do_update(
            index_elements=["chain"],
            set_={"last_scanned_block": block},
        )
        await db.execute(stmt)

    # ─── BSC 扫描 (eth_getLogs RPC) ─────────────────────

    async def _scan_bsc(
        self, db: AsyncSession, settings: SystemSettings, addresses: set[str]
    ):
        if not addresses:
            return

        last_block = await self._get_last_block(db, "BSC")
        current_block = await self._get_bsc_block_number(settings.bsc_rpc_urls)
        if current_block is None:
            logger.warning("无法获取 BSC 当前区块号，跳过本轮扫描")
            return

        if last_block == 0:
            start_block = max(1, current_block - DEFAULT_LOOKBACK_BSC)
        else:
            start_block = last_block + 1

        if start_block > current_block:
            return

        logger.info("BSC 扫描: 区块 %d → %d（%d 个地址）", start_block, current_block, len(addresses))

        system_addrs = await self._load_system_wallet_addresses(db, "BSC")
        contract = settings.bsc_usdt_contract
        new_count = 0
        actual_scanned = start_block - 1  # 实际成功扫描到的区块

        # 分批扫描，每批最多 2000 个区块（避免 RPC 返回数据过大）
        batch_size = 2000
        batch_start = start_block

        while batch_start <= current_block:
            batch_end = min(batch_start + batch_size - 1, current_block)
            logs = await self._get_bsc_logs(
                settings.bsc_rpc_urls, contract, batch_start, batch_end
            )

            if logs is None:
                logger.error("BSC eth_getLogs 失败，区块 %d-%d，停在 %d", batch_start, batch_end, actual_scanned)
                break

            for log_entry in logs:
                inserted = await self._process_bsc_log(db, log_entry, addresses, settings, system_addrs)
                if inserted:
                    new_count += 1

            actual_scanned = batch_end
            batch_start = batch_end + 1

        # 只更新到实际成功扫描的区块，失败的部分下次重扫
        if actual_scanned >= start_block:
            await self._set_last_block(db, "BSC", actual_scanned)
        if new_count > 0:
            logger.info("BSC 扫描完成: 新增 %d 笔充值（扫到区块 %d）", new_count, actual_scanned)

    async def _get_bsc_logs(
        self, rpc_urls: list, contract: str, from_block: int, to_block: int
    ) -> Optional[list]:
        """通过 eth_getLogs 获取 USDT Transfer 事件日志（多 RPC 容错）。"""
        for rpc_url in (rpc_urls or []):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(rpc_url, json={
                        "jsonrpc": "2.0",
                        "method": "eth_getLogs",
                        "params": [{
                            "fromBlock": hex(from_block),
                            "toBlock": hex(to_block),
                            "address": contract,
                            "topics": [TRANSFER_TOPIC],
                        }],
                        "id": 1,
                    })
                    data = resp.json()
                    if "error" in data:
                        logger.warning("BSC RPC %s eth_getLogs 错误: %s", rpc_url[:30], data["error"])
                        continue
                    return data.get("result", [])
            except Exception as e:
                logger.debug("BSC RPC %s eth_getLogs 失败: %s", rpc_url[:30], e)
                continue
        return None

    async def _process_bsc_log(
        self, db: AsyncSession, log_entry: dict, addresses: set[str],
        settings: SystemSettings, system_addrs: set[str] | None = None,
    ) -> bool:
        """处理单个 eth_getLogs 返回的 Transfer 事件。"""
        topics = log_entry.get("topics", [])
        if len(topics) < 3:
            return False

        # 解析地址 (topic 后 40 位 hex)
        from_addr = "0x" + topics[1][-40:]
        to_addr = "0x" + topics[2][-40:]

        if to_addr.lower() not in addresses:
            return False

        # 过滤系统内部转账（如 gas 钱包 → 充值地址补 BNB 等）
        if system_addrs and from_addr.lower() in system_addrs:
            logger.debug("跳过系统内部 BSC 转账: from=%s to=%s", from_addr[:12], to_addr[:12])
            return False

        # 解析金额 (BSC USDT = 18 位小数)
        raw_data = log_entry.get("data", "0x0")
        try:
            amount_wei = int(raw_data, 16)
        except (ValueError, TypeError):
            return False
        amount = Decimal(amount_wei) / Decimal(10 ** 18)

        if amount < MIN_DEPOSIT_AMOUNT:
            return False

        tx_hash = log_entry.get("transactionHash", "")
        block_hex = log_entry.get("blockNumber", "0x0")
        try:
            block_number = int(block_hex, 16)
        except (ValueError, TypeError):
            return False
        if not tx_hash or block_number == 0:
            return False

        # 幂等插入
        stmt = pg_insert(Deposit).values(
            chain="BSC",
            token="USDT",
            address=to_addr,
            from_address=from_addr,
            amount=amount,
            tx_hash=tx_hash,
            block_number=block_number,
            confirmations=0,
            status="pending",
        ).on_conflict_do_nothing(index_elements=["tx_hash"])

        result = await db.execute(stmt)
        inserted = bool(result.rowcount and result.rowcount > 0)

        if inserted:
            logger.info("新充值 BSC: %s USDT → %s (tx: %s)", amount, to_addr[:10], tx_hash[:16])
            # 每笔充值通知
            await notifier.notify_deposit(
                chain="BSC", address=to_addr, amount=amount,
                tx_hash=tx_hash, from_address=from_addr, token="USDT", db=db,
            )
            # 大额充值通知
            threshold = settings.large_deposit_threshold or Decimal("10000")
            if amount >= threshold:
                await notifier.notify_large_deposit(
                    chain="BSC", address=to_addr, amount=amount,
                    tx_hash=tx_hash, from_address=from_addr, token="USDT", db=db,
                )

        return inserted

    async def _get_bsc_block_number(self, rpc_urls: list) -> Optional[int]:
        """通过多 RPC 节点获取 BSC 当前区块号（顺序容错）。"""
        for rpc_url in (rpc_urls or []):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(rpc_url, json={
                        "jsonrpc": "2.0", "method": "eth_blockNumber",
                        "params": [], "id": 1,
                    })
                    data = resp.json()
                    hex_block = data.get("result", "0x0")
                    return int(hex_block, 16)
            except Exception as e:
                logger.debug("BSC RPC %s 失败: %s", rpc_url[:30], e)
                continue
        return None

    # ─── TRON 区块扫描（getblockbylimitnext 批量获取）────

    async def _scan_tron_blocks(
        self, db: AsyncSession, settings: SystemSettings, addresses: set[str]
    ):
        """扫描 TRON 区块，同时检测 USDT TRC20 转账和 TRX 原生转账。
        使用 getblockbylimitnext 批量获取区块数据，本地过滤。
        不管有多少地址，API 调用次数只取决于区块数量。
        """
        if not addresses:
            return

        last_block = await self._get_last_block(db, "TRON_BLOCK")
        current_block = await self._get_tron_block_number(
            settings.tron_api_urls, settings.tron_api_keys
        )
        if current_block is None:
            logger.warning("无法获取 TRON 当前区块号，跳过本轮扫描")
            return

        if last_block == 0:
            # 首次启动：同步到旧 TRON 扫描进度，或回溯少量区块
            tron_old = await self._get_last_block(db, "TRON")
            tron_native_old = await self._get_last_block(db, "TRON_NATIVE")
            sync_block = max(tron_old, tron_native_old)
            if sync_block > 0:
                start_block = sync_block
            else:
                start_block = max(1, current_block - 100)
        else:
            start_block = last_block + 1

        if start_block > current_block:
            return

        # 限制每轮最多 300 个区块（~15 分钟量），追赶时分多轮完成
        scan_end = min(current_block, start_block + 300)

        logger.debug(
            "TRON 区块扫描: %d → %d（%d 块, %d 个地址）",
            start_block, scan_end, scan_end - start_block + 1, len(addresses),
        )

        # 加载系统钱包地址，过滤内部转账（如 gas 钱包补 TRX → 充值地址）
        system_addrs_tron = await self._load_system_wallet_addresses(db, "TRON")

        # 构建地址查找集: base58 → hex41 (区块数据格式)
        hex41_addresses: set[str] = set()
        hex41_to_base58: dict[str, str] = {}
        for addr in addresses:
            h = self._tron_base58_to_hex41(addr)
            if h:
                hex41_addresses.add(h.lower())
                hex41_to_base58[h.lower()] = addr

        # USDT 合约 hex41
        usdt_hex41 = None
        if settings.tron_usdt_contract:
            usdt_hex41 = self._tron_base58_to_hex41(settings.tron_usdt_contract)
            if usdt_hex41:
                usdt_hex41 = usdt_hex41.lower()

        api_url = (settings.tron_api_urls or ["https://api.trongrid.io"])[0]
        api_keys = settings.tron_api_keys or []
        scan_native = settings.native_token_monitoring

        new_usdt = 0
        new_trx = 0
        actual_scanned = start_block - 1
        batch_size = 50  # 每次 API 获取 50 个区块
        request_idx = 0

        for batch_start in range(start_block, scan_end + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, scan_end)

            headers: dict[str, str] = {}
            if api_keys:
                headers["TRON-PRO-API-KEY"] = api_keys[request_idx % len(api_keys)]
                request_idx += 1

            blocks = await self._get_tron_blocks_batch(
                api_url, batch_start, batch_end, headers,
            )
            if blocks is None:
                logger.warning("TRON getblockbylimitnext 失败，停在区块 %d", actual_scanned)
                break

            for block_data in blocks:
                block_num = (
                    block_data.get("block_header", {})
                    .get("raw_data", {})
                    .get("number", 0)
                )
                for tx in block_data.get("transactions", []):
                    # 跳过失败交易
                    ret = tx.get("ret", [{}])
                    if not ret or ret[0].get("contractRet") != "SUCCESS":
                        continue

                    raw = tx.get("raw_data", {})
                    contracts = raw.get("contract", [])
                    if not contracts:
                        continue

                    c = contracts[0]
                    c_type = c.get("type", "")
                    param = c.get("parameter", {}).get("value", {})

                    if c_type == "TransferContract" and scan_native:
                        inserted = self._match_tron_trx_transfer(
                            tx, param, block_num, hex41_addresses, hex41_to_base58,
                        )
                        if inserted:
                            if inserted.get("from_address") in system_addrs_tron:
                                logger.debug(
                                    "跳过系统内部 TRON TRX 转账: from=%s to=%s",
                                    inserted["from_address"][:12], inserted["address"][:12],
                                )
                            else:
                                new_trx += await self._save_tron_deposit(
                                    db, settings, inserted,
                                )

                    elif c_type == "TriggerSmartContract" and usdt_hex41:
                        contract_addr = param.get("contract_address", "").lower()
                        if contract_addr == usdt_hex41:
                            inserted = self._match_tron_trc20_transfer(
                                tx, param, block_num,
                                hex41_addresses, hex41_to_base58,
                            )
                            if inserted:
                                if inserted.get("from_address") in system_addrs_tron:
                                    logger.debug(
                                        "跳过系统内部 TRON USDT 转账: from=%s to=%s",
                                        inserted["from_address"][:12], inserted["address"][:12],
                                    )
                                else:
                                    new_usdt += await self._save_tron_deposit(
                                        db, settings, inserted,
                                    )

            actual_scanned = batch_end

        if actual_scanned >= start_block:
            await self._set_last_block(db, "TRON_BLOCK", actual_scanned)
        if new_usdt > 0 or new_trx > 0:
            logger.info(
                "TRON 区块扫描完成: USDT +%d, TRX +%d（扫到区块 %d）",
                new_usdt, new_trx, actual_scanned,
            )

    async def _get_tron_blocks_batch(
        self, api_url: str, start_num: int, end_num: int, headers: dict,
    ) -> Optional[list]:
        """通过 getblockbylimitnext 批量获取 TRON 区块（含完整交易）。"""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{api_url}/wallet/getblockbylimitnext",
                    json={"startNum": start_num, "endNum": end_num + 1},
                    headers=headers,
                )
                if resp.status_code != 200:
                    logger.error("TRON getblockbylimitnext HTTP %d", resp.status_code)
                    return None
                data = resp.json()
                return data.get("block", [])
        except Exception as e:
            logger.error("TRON getblockbylimitnext 失败: %s", e)
            return None

    def _match_tron_trx_transfer(
        self, tx: dict, param: dict, block_num: int,
        hex41_addresses: set[str], hex41_to_base58: dict[str, str],
    ) -> Optional[dict]:
        """检查 TransferContract 是否为转入我们的地址，返回存储数据或 None。"""
        to_hex = param.get("to_address", "").lower()
        if to_hex not in hex41_addresses:
            return None

        amount_sun = param.get("amount", 0)
        if not amount_sun or amount_sun <= 0:
            return None

        amount = Decimal(amount_sun) / Decimal(10 ** 6)
        if amount < MIN_DEPOSIT_AMOUNT:
            return None

        tx_hash = tx.get("txID", "")
        if not tx_hash:
            return None

        owner_hex = param.get("owner_address", "")
        from_base58 = self._tron_hex41_to_base58(owner_hex) or owner_hex
        to_base58 = hex41_to_base58.get(to_hex, "")

        return {
            "token": "TRX",
            "address": to_base58,
            "from_address": from_base58,
            "amount": amount,
            "tx_hash": tx_hash,
            "block_number": block_num,
        }

    def _match_tron_trc20_transfer(
        self, tx: dict, param: dict, block_num: int,
        hex41_addresses: set[str], hex41_to_base58: dict[str, str],
    ) -> Optional[dict]:
        """检查 TriggerSmartContract 是否为 TRC20 transfer() 转入我们的地址。"""
        data_hex = param.get("data", "")
        # transfer(address,uint256) selector = a9059cbb
        # 最小长度: 8(selector) + 64(addr) + 64(amount) = 136
        if not data_hex or len(data_hex) < 136 or not data_hex.startswith("a9059cbb"):
            return None

        # 解析 to_address: selector(8) + padding(24) + address(40)
        to_20bytes = data_hex[32:72]
        to_hex41 = ("41" + to_20bytes).lower()

        if to_hex41 not in hex41_addresses:
            return None

        # 解析金额
        amount_hex = data_hex[72:136]
        try:
            amount_raw = int(amount_hex, 16)
        except ValueError:
            return None

        amount = Decimal(amount_raw) / Decimal(10 ** 6)  # USDT = 6 decimals
        if amount < MIN_DEPOSIT_AMOUNT:
            return None

        tx_hash = tx.get("txID", "")
        if not tx_hash:
            return None

        owner_hex = param.get("owner_address", "")
        from_base58 = self._tron_hex41_to_base58(owner_hex) or owner_hex
        to_base58 = hex41_to_base58.get(to_hex41, "")

        return {
            "token": "USDT",
            "address": to_base58,
            "from_address": from_base58,
            "amount": amount,
            "tx_hash": tx_hash,
            "block_number": block_num,
        }

    async def _save_tron_deposit(
        self, db: AsyncSession, settings: SystemSettings, data: dict,
    ) -> int:
        """保存 TRON 充值记录并发送通知。返回 1 表示新增，0 表示重复。"""
        stmt = pg_insert(Deposit).values(
            chain="TRON",
            token=data["token"],
            address=data["address"],
            from_address=data["from_address"],
            amount=data["amount"],
            tx_hash=data["tx_hash"],
            block_number=data["block_number"],
            confirmations=0,
            status="pending",
        ).on_conflict_do_nothing(index_elements=["tx_hash"])

        result = await db.execute(stmt)
        inserted = bool(result.rowcount and result.rowcount > 0)

        if inserted:
            token = data["token"]
            logger.info(
                "新充值 TRON: %s %s → %s (tx: %s)",
                data["amount"], token, data["address"][:10], data["tx_hash"][:16],
            )
            await notifier.notify_deposit(
                chain="TRON", address=data["address"], amount=data["amount"],
                tx_hash=data["tx_hash"], from_address=data["from_address"],
                token=token, db=db,
            )
            threshold = settings.large_deposit_threshold or Decimal("10000")
            if data["amount"] >= threshold:
                await notifier.notify_large_deposit(
                    chain="TRON", address=data["address"], amount=data["amount"],
                    tx_hash=data["tx_hash"], from_address=data["from_address"],
                    token=token, db=db,
                )

        return 1 if inserted else 0

    async def _get_tron_block_number(
        self, api_urls: list, api_keys: Optional[list] = None
    ) -> Optional[int]:
        """通过多 API 节点获取 TRON 当前区块号（顺序容错）。"""
        first_key = (api_keys or [None])[0]
        for api_url in (api_urls or []):
            try:
                headers: dict[str, str] = {}
                if first_key:
                    headers["TRON-PRO-API-KEY"] = first_key
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{api_url}/wallet/getnowblock", headers=headers,
                    )
                    data = resp.json()
                    block_header = data.get("block_header", {}).get("raw_data", {})
                    number = block_header.get("number", 0)
                    if number > 0:
                        return number
            except Exception as e:
                logger.debug("TRON API %s 失败: %s", api_url[:30], e)
                continue
        return None

    # ─── BSC BNB 原生转账扫描（JSON-RPC batch）────────────

    async def _scan_bsc_native(
        self, db: AsyncSession, settings: SystemSettings, addresses: set[str]
    ):
        """扫描 BNB 原生转入充值地址。
        使用 JSON-RPC batch 获取区块交易。
        首次启动时同步到 BSC USDT 的进度（避免漫长追赶），之后每轮只扫少量新区块。
        """
        if not addresses:
            return

        last_block = await self._get_last_block(db, "BSC_NATIVE")
        current_block = await self._get_bsc_block_number(settings.bsc_rpc_urls)
        if current_block is None:
            return

        if last_block == 0:
            # 首次启动：同步到 BSC USDT 扫描进度（或回溯 100 块）
            bsc_usdt_block = await self._get_last_block(db, "BSC")
            if bsc_usdt_block > 0:
                start_block = bsc_usdt_block
            else:
                start_block = max(1, current_block - DEFAULT_LOOKBACK_BSC)
        else:
            start_block = last_block + 1

        if start_block > current_block:
            return

        # 限制每轮最多扫 1000 个区块，追赶时快速跟上
        if current_block - start_block > 1000:
            current_block = start_block + 1000

        logger.debug("BSC BNB 扫描: 区块 %d → %d（%d 个地址）", start_block, current_block, len(addresses))

        system_addrs = await self._load_system_wallet_addresses(db, "BSC")
        new_count = 0
        actual_scanned = start_block - 1
        batch_size = 20  # 每次 batch RPC 获取 20 个区块
        rpc_urls = settings.bsc_rpc_urls or []

        for batch_start in range(start_block, current_block + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, current_block)

            # 构建 JSON-RPC batch 请求
            batch_requests = [
                {
                    "jsonrpc": "2.0",
                    "method": "eth_getBlockByNumber",
                    "params": [hex(bn), True],
                    "id": bn,
                }
                for bn in range(batch_start, batch_end + 1)
            ]

            results = None
            for rpc_url in rpc_urls:
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.post(rpc_url, json=batch_requests)
                        data = resp.json()
                        if isinstance(data, list):
                            results = data
                            break
                except Exception:
                    continue

            if not results:
                logger.warning("BSC BNB batch RPC 失败，停在区块 %d", actual_scanned)
                break

            for block_resp in results:
                block = block_resp.get("result")
                if not block:
                    continue
                for tx in block.get("transactions", []):
                    to_addr = tx.get("to")
                    if not to_addr or to_addr.lower() not in addresses:
                        continue
                    from_addr = tx.get("from", "")
                    if from_addr and from_addr.lower() in system_addrs:
                        logger.debug(
                            "跳过系统内部 BSC BNB 转账: from=%s to=%s",
                            from_addr[:12], to_addr[:12],
                        )
                        continue
                    inserted = await self._process_bsc_native_tx(db, tx, settings)
                    if inserted:
                        new_count += 1

            actual_scanned = batch_end

        if actual_scanned >= start_block:
            await self._set_last_block(db, "BSC_NATIVE", actual_scanned)
        if new_count > 0:
            logger.info("BSC BNB 扫描完成: 新增 %d 笔（扫到区块 %d）", new_count, actual_scanned)

    async def _process_bsc_native_tx(
        self, db: AsyncSession, tx: dict,
        settings: SystemSettings,
    ) -> bool:
        """处理 BSC 区块中的单个交易，检查是否为 BNB 转入充值地址。"""
        to_addr = tx.get("to", "")
        value_hex = tx.get("value", "0x0")
        try:
            value_wei = int(value_hex, 16)
        except (ValueError, TypeError):
            return False

        if value_wei == 0:
            return False

        amount = Decimal(value_wei) / Decimal(10 ** 18)
        if amount < MIN_DEPOSIT_AMOUNT:
            return False

        tx_hash = tx.get("hash", "")
        from_addr = tx.get("from", "")
        block_hex = tx.get("blockNumber", "0x0")
        try:
            block_number = int(block_hex, 16)
        except (ValueError, TypeError):
            return False

        if not tx_hash:
            return False

        stmt = pg_insert(Deposit).values(
            chain="BSC",
            token="BNB",
            address=to_addr.lower(),
            from_address=from_addr,
            amount=amount,
            tx_hash=tx_hash,
            block_number=block_number,
            confirmations=0,
            status="pending",
        ).on_conflict_do_nothing(index_elements=["tx_hash"])

        result = await db.execute(stmt)
        inserted = bool(result.rowcount and result.rowcount > 0)

        if inserted:
            logger.info("新充值 BSC: %s BNB → %s (tx: %s)", amount, to_addr[:10], tx_hash[:16])
            await notifier.notify_deposit(
                chain="BSC", address=to_addr, amount=amount,
                tx_hash=tx_hash, from_address=from_addr, token="BNB", db=db,
            )

        return inserted

    # ─── 确认数更新 ──────────────────────────────────────

    async def _update_confirmations(self, db: AsyncSession, settings: SystemSettings):
        """更新所有 pending/confirming 充值的确认数。BSC 和 TRON 独立处理。"""
        bsc_block = await self._get_bsc_block_number(settings.bsc_rpc_urls)
        tron_block = await self._get_tron_block_number(
            settings.tron_api_urls, settings.tron_api_keys
        )

        result = await db.execute(
            select(Deposit).where(Deposit.status.in_(["pending", "confirming"]))
        )
        deposits = result.scalars().all()

        bsc_required = settings.bsc_confirmations or 15
        tron_required = settings.tron_confirmations or 20
        now = datetime.now(timezone.utc)

        for deposit in deposits:
            if deposit.chain == "BSC":
                current_block = bsc_block
                required = bsc_required
            else:
                current_block = tron_block
                required = tron_required

            if current_block is None:
                # 该链 RPC 失败，跳过但不影响另一条链
                continue

            new_conf = max(0, current_block - deposit.block_number)
            deposit.confirmations = new_conf

            if new_conf >= required and deposit.status != "confirmed":
                deposit.status = "confirmed"
                deposit.confirmed_at = now
            elif new_conf > 0 and deposit.status == "pending":
                deposit.status = "confirming"


# ─── 模块级单例 ──────────────────────────────────────────

scanner = DepositScanner()
