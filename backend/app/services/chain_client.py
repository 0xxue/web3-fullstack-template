"""
链抽象层 — BSC / TRON 余额查询 + 转账

BSC:  web3.py (同步，包在 asyncio.to_thread) + RPCManager (来自 worker 生产验证)
TRON: 原生 httpx + eth-keys 签名（异步）

  - RPCManager: 端点健康追踪，失败 3 次拉黑，全挂时自动恢复
  - _do_transfer: 每次重试拿一个 RPC，nonce 缓存 + 冲突自动修复
  - 返回结构化结果 {"success", "tx_hash", "error"}
"""

import asyncio
import hashlib
import logging
import time
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import base58
import httpx
from eth_keys import keys as eth_keys
from web3 import Web3
from eth_account import Account

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.system_settings import SystemSettings
from app.services.tron_energy import tron_energy_service

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────

# Multicall3 (已部署在 BSC 主网)
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
MULTICALL3_ABI = [
    {
        "inputs": [
            {"components": [
                {"name": "target", "type": "address"},
                {"name": "allowFailure", "type": "bool"},
                {"name": "callData", "type": "bytes"},
            ], "name": "calls", "type": "tuple[]"},
        ],
        "name": "aggregate3",
        "outputs": [
            {"components": [
                {"name": "success", "type": "bool"},
                {"name": "returnData", "type": "bytes"},
            ], "name": "returnData", "type": "tuple[]"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{"name": "addr", "type": "address"}],
        "name": "getEthBalance",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Multicall 批量大小
MULTICALL_BATCH_SIZE = 200

ERC20_ABI = [
    {
        "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

BSC_CHAIN_ID = 56
BSC_GAS_LIMIT_ERC20 = 100_000
BSC_GAS_LIMIT_NATIVE = 21_000
BSC_GAS_PRICE_MULTIPLIER = 1.1
BSC_USDT_DECIMALS = 18

TRON_USDT_DECIMALS = 6
TRON_TRC20_FEE_LIMIT = 100_000_000  # 100 TRX
TRON_SUN = 1_000_000  # 1 TRX = 10^6 SUN

GAS_ESTIMATE_BSC = Decimal("0.0005")  # BNB per ERC-20 transfer (~1 gwei × 65000 gas)
GAS_ESTIMATE_TRON = Decimal("3")     # TRX per TRC-20 transfer（能量租赁后只需带宽，约 0.3-1 TRX）

# 补 gas 时的额外缓冲倍数
GAS_BUFFER_MULTIPLIER = Decimal("2")

# 归集原生代币时保留的 gas 费
NATIVE_RESERVE_BSC = Decimal("0.001")   # 保留 0.001 BNB
NATIVE_RESERVE_TRON = Decimal("3")      # 保留 3 TRX（归集原生 TRX 时留作带宽费）

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY = 1  # 秒


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BSC RPCManager — 搬自 worker/withdraw_worker.py（生产验证）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class RPCEndpoint:
    url: str
    web3: Optional[Web3] = field(default=None, repr=False)
    is_working: bool = True
    fail_count: int = 0

    def __post_init__(self):
        try:
            self.web3 = Web3(Web3.HTTPProvider(self.url, request_kwargs={"timeout": 30}))
        except Exception:
            self.is_working = False

    def mark_failed(self):
        self.fail_count += 1
        if self.fail_count >= 3:
            self.is_working = False

    def mark_success(self):
        self.fail_count = 0
        self.is_working = True


class RPCManager:
    """
    BSC RPC 管理器 — 搬自 worker（生产验证）

    特性:
      - 端点健康追踪: 失败 3 次自动拉黑
      - 轮换分配: 避免所有请求打到同一个 RPC
      - 自动恢复: 全部拉黑时重置所有端点
    """

    def __init__(self):
        self.endpoints: list[RPCEndpoint] = []
        self.current_index = 0

    def initialize(self, rpc_urls: list[str]) -> int:
        """初始化 RPC 端点列表，返回可用数量"""
        self.endpoints = []
        for url in (rpc_urls or []):
            ep = RPCEndpoint(url=url)
            if ep.web3:
                try:
                    ep.web3.eth.block_number  # 验证连通性
                    ep.is_working = True
                    self.endpoints.append(ep)
                except Exception:
                    pass
        count = len([e for e in self.endpoints if e.is_working])
        logger.info("RPCManager: %d/%d 个 BSC RPC 可用", count, len(rpc_urls or []))
        return count

    def get_rpc(self) -> Optional[RPCEndpoint]:
        """获取可用 RPC（轮换）"""
        working = [e for e in self.endpoints if e.is_working]
        if not working:
            # 全部挂了 → 重置，再试一轮
            for e in self.endpoints:
                e.is_working = True
                e.fail_count = 0
            working = self.endpoints
        if working:
            ep = working[self.current_index % len(working)]
            self.current_index = (self.current_index + 1) % len(working)
            return ep
        return None

    def get_gas_price(self) -> int:
        ep = self.get_rpc()
        if ep and ep.web3:
            base_price = ep.web3.eth.gas_price
            return int(base_price * BSC_GAS_PRICE_MULTIPLIER)
        return Web3.to_wei(1, "gwei")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BSC 转账函数 — 搬自 worker/_do_transfer（生产验证）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _bsc_do_transfer_native(
    rpc_mgr: RPCManager,
    sender_key: str,
    to_address: str,
    amount: Decimal,
    nonce_cache: dict,
    no_wait: bool = False,
) -> dict:
    """
    BSC 发送 BNB — 搬自 worker/_do_transfer 模式
    返回 {"success": bool, "tx_hash": str, "error": str}
    """
    try:
        if not sender_key.startswith("0x"):
            sender_key = "0x" + sender_key
        sender = Account.from_key(sender_key)
    except Exception as e:
        return {"success": False, "tx_hash": "", "error": f"私钥无效: {e}"}

    for attempt in range(MAX_RETRIES):
        endpoint = rpc_mgr.get_rpc()
        if not endpoint or not endpoint.web3:
            time.sleep(RETRY_DELAY)
            continue

        w3 = endpoint.web3
        try:
            # nonce 缓存
            if sender.address not in nonce_cache:
                nonce_cache[sender.address] = w3.eth.get_transaction_count(sender.address)
            nonce = nonce_cache[sender.address]

            gas_price = rpc_mgr.get_gas_price()
            to_checksum = Web3.to_checksum_address(to_address)
            value_wei = int(amount * Decimal(10 ** 18))
            # 动态估算 gas（目标可能是 Safe 合约，需要比 21000 更多）
            try:
                estimated = w3.eth.estimate_gas({
                    "from": sender.address,
                    "to": to_checksum,
                    "value": value_wei,
                })
                gas_limit = int(estimated * 1.2)  # 留 20% 余量
            except Exception:
                gas_limit = 100_000  # 合约接收 BNB 的安全回退值
            tx = {
                "to": to_checksum,
                "value": value_wei,
                "gas": gas_limit,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": BSC_CHAIN_ID,
            }
            signed = sender.sign_transaction(tx)
            raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
            tx_hash = w3.eth.send_raw_transaction(raw_tx)

            endpoint.mark_success()
            nonce_cache[sender.address] = nonce + 1

            if no_wait:
                return {"success": True, "tx_hash": tx_hash.hex(), "error": ""}

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt["status"] == 1:
                return {"success": True, "tx_hash": tx_hash.hex(), "error": ""}
            else:
                return {"success": False, "tx_hash": tx_hash.hex(), "error": "transaction_reverted"}

        except Exception as e:
            error_msg = str(e)
            endpoint.mark_failed()

            if "nonce" in error_msg.lower() or "already known" in error_msg.lower():
                nonce_cache.pop(sender.address, None)

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                return {"success": False, "tx_hash": "", "error": f"重试{MAX_RETRIES}次失败: {error_msg[:200]}"}

    return {"success": False, "tx_hash": "", "error": "无可用RPC"}


def _bsc_do_transfer_usdt(
    rpc_mgr: RPCManager,
    usdt_contract: str,
    sender_key: str,
    to_address: str,
    amount: Decimal,
    nonce_cache: dict,
) -> dict:
    """
    BSC 发送 USDT — 搬自 worker/_do_transfer 模式
    返回 {"success": bool, "tx_hash": str, "error": str}
    """
    try:
        if not sender_key.startswith("0x"):
            sender_key = "0x" + sender_key
        sender = Account.from_key(sender_key)
    except Exception as e:
        return {"success": False, "tx_hash": "", "error": f"私钥无效: {e}"}

    amount_wei = int(amount * Decimal(10 ** BSC_USDT_DECIMALS))

    for attempt in range(MAX_RETRIES):
        endpoint = rpc_mgr.get_rpc()
        if not endpoint or not endpoint.web3:
            time.sleep(RETRY_DELAY)
            continue

        w3 = endpoint.web3
        try:
            token = w3.eth.contract(
                address=Web3.to_checksum_address(usdt_contract),
                abi=ERC20_ABI,
            )

            # nonce 缓存
            if sender.address not in nonce_cache:
                nonce_cache[sender.address] = w3.eth.get_transaction_count(sender.address)
            nonce = nonce_cache[sender.address]

            gas_price = rpc_mgr.get_gas_price()
            tx = token.functions.transfer(
                Web3.to_checksum_address(to_address), amount_wei,
            ).build_transaction({
                "from": sender.address,
                "gas": BSC_GAS_LIMIT_ERC20,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": BSC_CHAIN_ID,
            })
            signed = sender.sign_transaction(tx)
            raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
            tx_hash = w3.eth.send_raw_transaction(raw_tx)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            endpoint.mark_success()
            nonce_cache[sender.address] = nonce + 1

            if receipt["status"] == 1:
                return {"success": True, "tx_hash": tx_hash.hex(), "error": ""}
            else:
                return {"success": False, "tx_hash": tx_hash.hex(), "error": "transaction_reverted"}

        except Exception as e:
            error_msg = str(e)
            endpoint.mark_failed()

            if "nonce" in error_msg.lower() or "already known" in error_msg.lower():
                nonce_cache.pop(sender.address, None)

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                return {"success": False, "tx_hash": "", "error": f"重试{MAX_RETRIES}次失败: {error_msg[:200]}"}

    return {"success": False, "tx_hash": "", "error": "无可用RPC"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Nonce 缓存 — 跨调用复用（归集期间同一 gas 钱包的 nonce）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 每次归集批次创建新的 nonce_cache dict 传入，批次结束自动丢弃
# 不需要全局 NonceManager，和 worker 一样用局部 dict


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRON 地址工具
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def tron_base58_to_hex(base58_addr: str) -> str:
    decoded = base58.b58decode_check(base58_addr)
    return decoded[1:].hex()


def tron_hex_to_base58(hex20: str) -> str:
    clean = hex20.replace("0x", "")
    addr_bytes = bytes.fromhex("41" + clean)
    return base58.b58encode_check(addr_bytes).decode("ascii")


def _tron_abi_encode_address(base58_addr: str) -> str:
    hex20 = tron_base58_to_hex(base58_addr)
    return hex20.zfill(64)


def _tron_abi_encode_uint256(value: int) -> str:
    return hex(value)[2:].zfill(64)


def _tron_sign_transaction(raw_data_hex: str, private_key_hex: str) -> str:
    raw_bytes = bytes.fromhex(raw_data_hex)
    tx_hash = hashlib.sha256(raw_bytes).digest()
    pk = eth_keys.PrivateKey(bytes.fromhex(private_key_hex))
    signature = pk.sign_msg_hash(tx_hash)
    return signature.to_bytes().hex()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ChainClient — 高层 API（供 executor / proposal_service 调用）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ChainClient:
    """
    链客户端。

    BSC: 内部使用 RPCManager (生产验证), 调用方可传入 nonce_cache 复用。
    TRON: httpx 异步调用，多节点容错。
    """

    def __init__(self):
        self._bsc_rpc_mgr: Optional[RPCManager] = None
        self._bsc_rpc_urls_hash: Optional[int] = None

    async def _load_settings(self) -> SystemSettings:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemSettings).where(SystemSettings.id == 1)
            )
            settings = result.scalar_one_or_none()
            if not settings:
                raise RuntimeError("SystemSettings 未初始化")
            return settings

    def _get_bsc_rpc_mgr(self, rpc_urls: list[str]) -> RPCManager:
        """懒初始化 BSC RPCManager，RPC 列表变化时重建"""
        urls_hash = hash(tuple(rpc_urls or []))
        if self._bsc_rpc_mgr is None or self._bsc_rpc_urls_hash != urls_hash:
            mgr = RPCManager()
            count = mgr.initialize(rpc_urls)
            if count == 0:
                raise RuntimeError("BSC 无可用 RPC 节点")
            self._bsc_rpc_mgr = mgr
            self._bsc_rpc_urls_hash = urls_hash
        return self._bsc_rpc_mgr

    # ─── 余额查询 ─────────────────────────────────────

    async def get_usdt_balance(self, chain: str, address: str) -> Decimal:
        settings = await self._load_settings()
        if chain == "BSC":
            return await self._bsc_usdt_balance(settings.bsc_rpc_urls, settings.bsc_usdt_contract, address)
        elif chain == "TRON":
            return await self._tron_usdt_balance(settings.tron_api_urls, settings.tron_api_keys, settings.tron_usdt_contract, address)
        raise ValueError(f"不支持的链: {chain}")

    async def get_native_balance(self, chain: str, address: str) -> Decimal:
        settings = await self._load_settings()
        if chain == "BSC":
            return await self._bsc_native_balance(settings.bsc_rpc_urls, address)
        elif chain == "TRON":
            return await self._tron_native_balance(settings.tron_api_urls, settings.tron_api_keys, address)
        raise ValueError(f"不支持的链: {chain}")

    # ─── 批量余额查询（扫描专用）─────────────────────

    async def batch_get_balances(
        self, chain: str, addresses: list[str],
    ) -> list[dict]:
        """
        批量查询地址的 USDT + native 余额。
        返回: [{"address": str, "usdt": Decimal, "native": Decimal}, ...]

        BSC: Multicall3 合约批量查询，200 个/批，一次 RPC 调用
        TRON: 并发 30 个查询
        """
        settings = await self._load_settings()
        if chain == "BSC":
            return await self._bsc_batch_balances(
                settings.bsc_rpc_urls, settings.bsc_usdt_contract, addresses,
            )
        elif chain == "TRON":
            return await self._tron_batch_balances(
                settings.tron_api_urls, settings.tron_api_keys,
                settings.tron_usdt_contract, addresses,
            )
        raise ValueError(f"不支持的链: {chain}")

    async def _bsc_batch_balances(
        self, rpc_urls: list, usdt_contract: str, addresses: list[str],
    ) -> list[dict]:
        """BSC Multicall3 批量查询 USDT + BNB 余额"""
        def _call():
            rpc_mgr = self._get_bsc_rpc_mgr(rpc_urls)
            results = []

            # balanceOf(address) selector = 0x70a08231
            balance_of_selector = bytes.fromhex("70a08231")
            usdt_addr = Web3.to_checksum_address(usdt_contract)

            for batch_start in range(0, len(addresses), MULTICALL_BATCH_SIZE):
                batch = addresses[batch_start:batch_start + MULTICALL_BATCH_SIZE]

                # 构建 multicall 请求: 每个地址 2 个调用 (USDT balanceOf + getEthBalance)
                calls = []
                for addr in batch:
                    checksum_addr = Web3.to_checksum_address(addr)
                    # 1) USDT balanceOf
                    call_data = balance_of_selector + bytes.fromhex(
                        checksum_addr[2:].lower().zfill(64)
                    )
                    calls.append((usdt_addr, True, call_data))
                    # 2) native balance via Multicall3.getEthBalance
                    get_bal_data = bytes.fromhex("4d2301cc") + bytes.fromhex(
                        checksum_addr[2:].lower().zfill(64)
                    )
                    calls.append((Web3.to_checksum_address(MULTICALL3_ADDRESS), True, get_bal_data))

                # 执行 multicall（带重试）
                batch_results = None
                for _ in range(MAX_RETRIES):
                    ep = rpc_mgr.get_rpc()
                    if not ep or not ep.web3:
                        continue
                    try:
                        mc = ep.web3.eth.contract(
                            address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
                            abi=MULTICALL3_ABI,
                        )
                        batch_results = mc.functions.aggregate3(calls).call()
                        ep.mark_success()
                        break
                    except Exception as e:
                        ep.mark_failed()
                        logger.warning("BSC multicall 批次失败: %s", str(e)[:100])

                if batch_results is None:
                    # multicall 全部失败，回退到逐个查询
                    logger.warning("BSC multicall 不可用，回退逐个查询 %d 个地址", len(batch))
                    for addr in batch:
                        try:
                            checksum = Web3.to_checksum_address(addr)
                            ep2 = rpc_mgr.get_rpc()
                            if ep2 and ep2.web3:
                                token = ep2.web3.eth.contract(address=usdt_addr, abi=ERC20_ABI)
                                usdt_raw = token.functions.balanceOf(checksum).call()
                                native_raw = ep2.web3.eth.get_balance(checksum)
                                ep2.mark_success()
                                results.append({
                                    "address": addr,
                                    "usdt": Decimal(usdt_raw) / Decimal(10 ** BSC_USDT_DECIMALS),
                                    "native": Decimal(native_raw) / Decimal(10 ** 18),
                                })
                            else:
                                results.append({"address": addr, "usdt": Decimal(0), "native": Decimal(0)})
                        except Exception:
                            results.append({"address": addr, "usdt": Decimal(0), "native": Decimal(0)})
                    continue

                # 解析 multicall 结果（每个地址 2 个结果）
                for i, addr in enumerate(batch):
                    usdt_result = batch_results[i * 2]
                    native_result = batch_results[i * 2 + 1]

                    usdt_bal = Decimal(0)
                    native_bal = Decimal(0)

                    if usdt_result[0] and len(usdt_result[1]) >= 32:
                        usdt_raw = int.from_bytes(usdt_result[1][:32], "big")
                        usdt_bal = Decimal(usdt_raw) / Decimal(10 ** BSC_USDT_DECIMALS)

                    if native_result[0] and len(native_result[1]) >= 32:
                        native_raw = int.from_bytes(native_result[1][:32], "big")
                        native_bal = Decimal(native_raw) / Decimal(10 ** 18)

                    results.append({
                        "address": addr,
                        "usdt": usdt_bal,
                        "native": native_bal,
                    })

            return results

        return await asyncio.to_thread(_call)

    async def _tron_batch_balances(
        self, api_urls: list, api_keys: list, usdt_contract: str,
        addresses: list[str],
    ) -> list[dict]:
        """
        TRON 批量查询余额 — JSON-RPC batch（一次 HTTP 请求查多个地址）
        回退: 逐个并发查询（Semaphore=30）
        """
        # 先尝试 JSON-RPC batch
        try:
            return await self._tron_batch_jsonrpc(api_urls, api_keys, usdt_contract, addresses)
        except Exception as e:
            logger.warning("TRON JSON-RPC batch 失败，回退逐个查询: %s", e)

        # 回退: 并发逐个查询
        sem = asyncio.Semaphore(30)

        async def query_one(addr: str) -> dict:
            async with sem:
                try:
                    usdt_bal = await self._tron_usdt_balance(api_urls, api_keys, usdt_contract, addr)
                except Exception:
                    usdt_bal = Decimal(0)
                try:
                    native_bal = await self._tron_native_balance(api_urls, api_keys, addr)
                except Exception:
                    native_bal = Decimal(0)
                return {"address": addr, "usdt": usdt_bal, "native": native_bal}

        tasks = [query_one(a) for a in addresses]
        return list(await asyncio.gather(*tasks))

    async def _tron_batch_jsonrpc(
        self, api_urls: list, api_keys: list, usdt_contract: str,
        addresses: list[str],
    ) -> list[dict]:
        """TRON JSON-RPC batch: 每批 100 个地址，每个地址 2 个请求"""
        TRON_BATCH_SIZE = 100

        usdt_evm = "0x" + tron_base58_to_hex(usdt_contract)  # 0x + 20bytes hex
        results = []

        for batch_start in range(0, len(addresses), TRON_BATCH_SIZE):
            batch = addresses[batch_start:batch_start + TRON_BATCH_SIZE]

            # 构建 batch 请求
            rpc_calls = []
            req_id = 1
            for addr in batch:
                addr_hex20 = tron_base58_to_hex(addr)  # 20 bytes hex (无 0x)
                # TRON JSON-RPC 需要完整 41 前缀的 hex 地址
                addr_full_hex = "0x41" + addr_hex20

                # 1) USDT balanceOf(address)
                call_data = "0x70a08231" + addr_hex20.zfill(64)
                rpc_calls.append({
                    "jsonrpc": "2.0", "id": req_id,
                    "method": "eth_call",
                    "params": [{"to": usdt_evm, "data": call_data}, "latest"],
                })
                req_id += 1

                # 2) TRX balance
                rpc_calls.append({
                    "jsonrpc": "2.0", "id": req_id,
                    "method": "eth_getBalance",
                    "params": [addr_full_hex, "latest"],
                })
                req_id += 1

            # 尝试多个 API 节点
            batch_results = None
            for i, api_url in enumerate(api_urls or []):
                headers: dict[str, str] = {"Content-Type": "application/json"}
                if api_keys:
                    headers["TRON-PRO-API-KEY"] = api_keys[i % len(api_keys)]
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.post(
                            f"{api_url}/jsonrpc", json=rpc_calls, headers=headers,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list) and len(data) == len(rpc_calls):
                                batch_results = data
                                break
                except Exception as e:
                    logger.debug("TRON JSON-RPC batch %s 失败: %s", api_url[:30], e)

            if batch_results is None:
                raise RuntimeError("TRON JSON-RPC batch 所有节点失败")

            # 解析结果
            for i, addr in enumerate(batch):
                usdt_resp = batch_results[i * 2]
                trx_resp = batch_results[i * 2 + 1]

                usdt_raw = int(usdt_resp.get("result", "0x0") or "0x0", 16)
                trx_raw = int(trx_resp.get("result", "0x0") or "0x0", 16)

                results.append({
                    "address": addr,
                    "usdt": Decimal(usdt_raw) / Decimal(10 ** TRON_USDT_DECIMALS),
                    "native": Decimal(trx_raw) / Decimal(TRON_SUN),
                })

        return results

    # ─── 发送转账 ─────────────────────────────────────

    async def send_native(
        self, chain: str, from_private_key: str, from_address: str,
        to_address: str, amount: Decimal,
        nonce_cache: dict | None = None,
        wait_receipt: bool = True,
    ) -> str:
        """发送 BNB/TRX。wait_receipt=False 广播后立即返回不等链上确认"""
        settings = await self._load_settings()
        if chain == "BSC":
            return await self._bsc_send_native(
                settings.bsc_rpc_urls, from_private_key, to_address, amount,
                nonce_cache=nonce_cache, no_wait=not wait_receipt,
            )
        elif chain == "TRON":
            return await self._tron_send_native(
                settings.tron_api_urls, settings.tron_api_keys,
                from_private_key, from_address, to_address, amount,
            )
        raise ValueError(f"不支持的链: {chain}")

    async def send_usdt(
        self, chain: str, from_private_key: str, from_address: str,
        to_address: str, amount: Decimal,
        nonce_cache: dict | None = None,
        skip_energy_rental: bool = False,
    ) -> str:
        """发送 USDT。nonce_cache: BSC 时可传入共享缓存"""
        settings = await self._load_settings()
        if chain == "BSC":
            return await self._bsc_send_usdt(
                settings.bsc_rpc_urls, settings.bsc_usdt_contract,
                from_private_key, to_address, amount,
                nonce_cache=nonce_cache,
            )
        elif chain == "TRON":
            return await self._tron_send_usdt(
                settings.tron_api_urls, settings.tron_api_keys,
                settings.tron_usdt_contract,
                from_private_key, from_address, to_address, amount,
                skip_energy_rental=skip_energy_rental,
            )
        raise ValueError(f"不支持的链: {chain}")

    # ─── BSC 实现（使用 RPCManager + _do_transfer 模式）──

    async def _bsc_usdt_balance(self, rpc_urls: list, contract: str, address: str) -> Decimal:
        def _call():
            rpc_mgr = self._get_bsc_rpc_mgr(rpc_urls)
            for _ in range(MAX_RETRIES):
                ep = rpc_mgr.get_rpc()
                if not ep or not ep.web3:
                    continue
                try:
                    token = ep.web3.eth.contract(
                        address=Web3.to_checksum_address(contract), abi=ERC20_ABI,
                    )
                    raw = token.functions.balanceOf(Web3.to_checksum_address(address)).call()
                    ep.mark_success()
                    return Decimal(raw) / Decimal(10 ** BSC_USDT_DECIMALS)
                except Exception as e:
                    ep.mark_failed()
                    logger.debug("BSC balanceOf %s 失败: %s", address[:10], e)
            raise RuntimeError(f"BSC 查询 balanceOf({address[:10]}) 失败")
        return await asyncio.to_thread(_call)

    async def _bsc_native_balance(self, rpc_urls: list, address: str) -> Decimal:
        def _call():
            rpc_mgr = self._get_bsc_rpc_mgr(rpc_urls)
            for _ in range(MAX_RETRIES):
                ep = rpc_mgr.get_rpc()
                if not ep or not ep.web3:
                    continue
                try:
                    raw = ep.web3.eth.get_balance(Web3.to_checksum_address(address))
                    ep.mark_success()
                    return Decimal(raw) / Decimal(10 ** 18)
                except Exception as e:
                    ep.mark_failed()
                    logger.debug("BSC get_balance %s 失败: %s", address[:10], e)
            raise RuntimeError(f"BSC 查询 BNB 余额({address[:10]}) 失败")
        return await asyncio.to_thread(_call)

    async def _bsc_send_native(
        self, rpc_urls: list, from_key: str, to_address: str, amount: Decimal,
        nonce_cache: dict | None = None, no_wait: bool = False,
    ) -> str:
        """BSC 发送 BNB — 使用 worker 的 _do_transfer 模式"""
        def _call():
            rpc_mgr = self._get_bsc_rpc_mgr(rpc_urls)
            cache = nonce_cache if nonce_cache is not None else {}
            result = _bsc_do_transfer_native(rpc_mgr, from_key, to_address, amount, cache, no_wait=no_wait)
            if not result["success"]:
                raise RuntimeError(result["error"])
            return result["tx_hash"]
        return await asyncio.to_thread(_call)

    async def _bsc_send_usdt(
        self, rpc_urls: list, contract: str,
        from_key: str, to_address: str, amount: Decimal,
        nonce_cache: dict | None = None,
    ) -> str:
        """BSC 发送 USDT — 使用 worker 的 _do_transfer 模式"""
        def _call():
            rpc_mgr = self._get_bsc_rpc_mgr(rpc_urls)
            cache = nonce_cache if nonce_cache is not None else {}
            result = _bsc_do_transfer_usdt(rpc_mgr, contract, from_key, to_address, amount, cache)
            if not result["success"]:
                raise RuntimeError(result["error"])
            return result["tx_hash"]
        return await asyncio.to_thread(_call)

    # ─── TRON 实现 ─────────────────────────────────────

    async def _tron_api_call(
        self, api_urls: list, api_keys: list,
        path: str, payload: dict, method: str = "POST",
    ) -> dict:
        """通用 TRON API 调用，多节点容错"""
        for i, api_url in enumerate(api_urls or []):
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_keys:
                headers["TRON-PRO-API-KEY"] = api_keys[i % len(api_keys)]
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    if method == "POST":
                        resp = await client.post(f"{api_url}{path}", json=payload, headers=headers)
                    else:
                        resp = await client.get(f"{api_url}{path}", params=payload, headers=headers)
                    if resp.status_code != 200:
                        logger.debug("TRON API %s%s HTTP %d", api_url[:30], path, resp.status_code)
                        continue
                    return resp.json()
            except Exception as e:
                logger.debug("TRON API %s%s 失败: %s", api_url[:30], path, e)
                continue
        raise RuntimeError(f"TRON 所有 API 节点 {path} 调用失败")

    async def _tron_usdt_balance(self, api_urls: list, api_keys: list, contract: str, address: str) -> Decimal:
        param = _tron_abi_encode_address(address)
        data = await self._tron_api_call(api_urls, api_keys, "/wallet/triggersmartcontract", {
            "owner_address": address, "contract_address": contract,
            "function_selector": "balanceOf(address)", "parameter": param, "visible": True,
        })
        results = data.get("constant_result", [])
        if not results:
            return Decimal(0)
        raw_value = int(results[0], 16) if results[0] else 0
        return Decimal(raw_value) / Decimal(10 ** TRON_USDT_DECIMALS)

    async def _tron_native_balance(self, api_urls: list, api_keys: list, address: str) -> Decimal:
        try:
            data = await self._tron_api_call(api_urls, api_keys, "/wallet/getaccount", {
                "address": address, "visible": True,
            })
        except RuntimeError:
            return Decimal(0)
        return Decimal(data.get("balance", 0)) / Decimal(TRON_SUN)

    async def _tron_send_native(
        self, api_urls: list, api_keys: list,
        from_key: str, from_address: str, to_address: str, amount: Decimal,
    ) -> str:
        """发送 TRX，带重试"""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                amount_sun = int(amount * Decimal(TRON_SUN))
                data = await self._tron_api_call(api_urls, api_keys, "/wallet/createtransaction", {
                    "to_address": to_address, "owner_address": from_address,
                    "amount": amount_sun, "visible": True,
                })
                if "Error" in data or "error" in data:
                    raise RuntimeError(f"TRON createtransaction 失败: {data}")
                raw_data_hex = data.get("raw_data_hex", "")
                if not raw_data_hex:
                    raise RuntimeError(f"TRON createtransaction 无 raw_data_hex")

                sig = _tron_sign_transaction(raw_data_hex, from_key)
                data["signature"] = [sig]
                result = await self._tron_api_call(api_urls, api_keys, "/wallet/broadcasttransaction", data)
                if not result.get("result"):
                    raise RuntimeError(f"TRON TRX 广播失败: {result}")
                return result.get("txid", data.get("txID", ""))
            except Exception as e:
                last_error = e
                logger.warning("TRON send_native 失败 (attempt %d): %s", attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError(f"TRON 发送 TRX 失败(重试{MAX_RETRIES}次): {last_error}")

    async def _tron_send_usdt(
        self, api_urls: list, api_keys: list, contract: str,
        from_key: str, from_address: str, to_address: str, amount: Decimal,
        skip_energy_rental: bool = False,
    ) -> str:
        """发送 TRC20 USDT，带重试"""
        # 转账前尝试租赁能量（归集场景已预租赁，跳过）
        if not skip_energy_rental:
            try:
                s = await self._load_settings()
                energy_result = await tron_energy_service.ensure_energy(
                    api_urls, api_keys, from_address,
                    rental_enabled=s.tron_energy_rental_enabled,
                    rental_api_url=(s.tron_energy_rental_api_url or "").strip(),
                    rental_api_key=(s.tron_energy_rental_api_key or "").strip(),
                    rental_max_price_sun=s.tron_energy_rental_max_price or 150,
                    rental_duration_ms=s.tron_energy_rental_duration or 3_600_000,
                )
                if energy_result.get("sufficient"):
                    logger.info("TRON 能量就绪: available=%d", energy_result.get("available", 0))
                elif energy_result.get("error"):
                    logger.warning("TRON 能量不足（将烧 TRX 执行）: %s", energy_result["error"])
            except Exception as e:
                logger.warning("TRON 能量检查/租赁异常(不影响转账): %s", e)

        amount_raw = int(amount * Decimal(10 ** TRON_USDT_DECIMALS))
        param = _tron_abi_encode_address(to_address) + _tron_abi_encode_uint256(amount_raw)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                data = await self._tron_api_call(api_urls, api_keys, "/wallet/triggersmartcontract", {
                    "owner_address": from_address, "contract_address": contract,
                    "function_selector": "transfer(address,uint256)", "parameter": param,
                    "fee_limit": TRON_TRC20_FEE_LIMIT, "call_value": 0, "visible": True,
                })
                if "Error" in data or "error" in data:
                    raise RuntimeError(f"TRON triggersmartcontract 失败: {data}")
                tx = data.get("transaction")
                if not tx:
                    raise RuntimeError(f"TRON triggersmartcontract 无 transaction")
                raw_data_hex = tx.get("raw_data_hex", "")
                if not raw_data_hex:
                    raise RuntimeError("TRON 交易缺少 raw_data_hex")

                sig = _tron_sign_transaction(raw_data_hex, from_key)
                tx["signature"] = [sig]
                result = await self._tron_api_call(api_urls, api_keys, "/wallet/broadcasttransaction", tx)
                if not result.get("result"):
                    raise RuntimeError(f"TRON USDT 广播失败: {result}")
                return result.get("txid", tx.get("txID", ""))
            except Exception as e:
                last_error = e
                logger.warning("TRON send_usdt 失败 (attempt %d): %s", attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError(f"TRON 发送 USDT 失败(重试{MAX_RETRIES}次): {last_error}")


# ─── 模块级单例 ──────────────────────────────────────

chain_client = ChainClient()
