"""
多签钱包服务 — BSC Gnosis Safe 部署/验证 + TRON 合约多签部署

BSC: 通过 ProxyFactory 部署 Safe v1.3.0 代理合约
TRON: 部署 TronMultiSig.sol 合约（强制租赁能量，避免高额 TRX 消耗）
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
from web3 import Web3
from eth_account import Account

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.system_settings import SystemSettings
from app.services.safe_constants import (
    SAFE_PROXY_FACTORY, SAFE_SINGLETON, SAFE_FALLBACK_HANDLER,
    SAFE_ABI, PROXY_FACTORY_ABI,
)
from app.services.chain_client import (
    _tron_sign_transaction, tron_base58_to_hex, tron_hex_to_base58,
    _tron_abi_encode_address, _tron_abi_encode_uint256,
)
from app.services.tron_energy import tron_energy_service

# ── TronMultiSig 合约编译产物 ──────────────────────────────────────────────
_COMPILED_PATH = Path(__file__).parent.parent.parent / "contracts" / "TronMultiSig_compiled.json"
try:
    _compiled = json.loads(_COMPILED_PATH.read_text())
    TRON_MULTISIG_BYTECODE: str = _compiled["bytecode"]
    TRON_MULTISIG_ABI: list = _compiled["abi"]
except Exception as _e:
    raise RuntimeError(f"无法加载 TronMultiSig 编译产物 ({_COMPILED_PATH}): {_e}")

# 部署所需能量（11.8KB bytecode × 200 + 构造函数 ≈ 1.8M，取 2M）
TRON_DEPLOY_ENERGY = 2_000_000

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# TRON active_permission operations 位掩码：允许常规操作（转账、合约调用等）
TRON_ACTIVE_OPERATIONS = (
    "7fff1fc0033e0300000000000000000000000000000000000000000000000000"
)


class MultisigService:

    async def _load_settings(self) -> SystemSettings:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemSettings).where(SystemSettings.id == 1)
            )
            settings = result.scalar_one_or_none()
            if not settings:
                raise RuntimeError("SystemSettings 未初始化")
            return settings

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BSC Gnosis Safe
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def deploy_bsc_safe(
        self,
        owners: list[str],
        threshold: int,
        gas_private_key: str,
        salt_nonce: int | None = None,
    ) -> tuple[str, str]:
        """
        部署 Gnosis Safe v1.3.0 代理合约到 BSC。

        返回: (safe_address, tx_hash)
        """
        settings = await self._load_settings()

        def _deploy():
            if not gas_private_key.startswith("0x"):
                key = "0x" + gas_private_key
            else:
                key = gas_private_key
            sender = Account.from_key(key)
            nonce = salt_nonce or int(time.time())

            for rpc_url in (settings.bsc_rpc_urls or []):
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))

                    # 编码 Safe.setup() initializer
                    safe_contract = w3.eth.contract(
                        address=Web3.to_checksum_address(SAFE_SINGLETON),
                        abi=SAFE_ABI,
                    )
                    initializer = safe_contract.encodeABI(
                        fn_name="setup",
                        args=[
                            [Web3.to_checksum_address(o) for o in owners],
                            threshold,
                            Web3.to_checksum_address(ZERO_ADDRESS),  # to
                            b"",                                      # data
                            Web3.to_checksum_address(SAFE_FALLBACK_HANDLER),
                            Web3.to_checksum_address(ZERO_ADDRESS),  # paymentToken
                            0,                                        # payment
                            Web3.to_checksum_address(ZERO_ADDRESS),  # paymentReceiver
                        ],
                    )

                    # 调用 ProxyFactory.createProxyWithNonce
                    factory = w3.eth.contract(
                        address=Web3.to_checksum_address(SAFE_PROXY_FACTORY),
                        abi=PROXY_FACTORY_ABI,
                    )
                    gas_price = int(w3.eth.gas_price * 1.1)
                    tx = factory.functions.createProxyWithNonce(
                        Web3.to_checksum_address(SAFE_SINGLETON),
                        bytes.fromhex(initializer[2:]),  # 去掉 0x 前缀
                        nonce,
                    ).build_transaction({
                        "from": sender.address,
                        "gas": 500_000,
                        "gasPrice": gas_price,
                        "nonce": w3.eth.get_transaction_count(sender.address),
                        "chainId": 56,
                    })

                    signed = sender.sign_transaction(tx)
                    raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
                    tx_hash = w3.eth.send_raw_transaction(raw_tx)
                    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

                    if receipt["status"] != 1:
                        raise RuntimeError("Safe 部署交易 reverted")

                    # 从 ProxyCreation 事件解析 Safe 地址
                    proxy_logs = factory.events.ProxyCreation().process_receipt(receipt)
                    if not proxy_logs:
                        raise RuntimeError("未找到 ProxyCreation 事件")
                    safe_address = proxy_logs[0]["args"]["proxy"]

                    logger.info("BSC Safe 部署成功: %s (tx: %s)", safe_address, tx_hash.hex())
                    return safe_address, tx_hash.hex()

                except Exception as e:
                    logger.warning("BSC Safe deploy via %s 失败: %s", rpc_url[:30], e)
                    continue

            raise RuntimeError("BSC Safe 部署失败：所有 RPC 均不可用")

        return await asyncio.to_thread(_deploy)

    async def verify_bsc_safe(self, address: str) -> dict:
        """
        链上验证 BSC Gnosis Safe，读取 owners 和 threshold。

        返回: {"owners": [...], "threshold": int}
        """
        settings = await self._load_settings()

        def _verify():
            for rpc_url in (settings.bsc_rpc_urls or []):
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
                    safe = w3.eth.contract(
                        address=Web3.to_checksum_address(address),
                        abi=SAFE_ABI,
                    )
                    owners = safe.functions.getOwners().call()
                    threshold = safe.functions.getThreshold().call()
                    return {
                        "owners": [str(o) for o in owners],
                        "threshold": threshold,
                    }
                except Exception as e:
                    logger.debug("BSC Safe verify %s via %s 失败: %s", address[:10], rpc_url[:30], e)
                    continue
            raise RuntimeError(f"无法验证 BSC Safe 合约 {address[:10]}...")

        return await asyncio.to_thread(_verify)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRON 合约多签部署
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _abi_encode_constructor(self, owners: list[str], threshold: int) -> str:
        """
        ABI 编码构造函数参数 (address[] _owners, uint256 _threshold)。
        TRON 地址在 ABI 中以 20 字节形式表示（去掉网络前缀 41）。
        """
        n = len(owners)
        # offset to array data = 32 (offset slot) + 32 (threshold slot) = 64 bytes = 0x40
        offset = _tron_abi_encode_uint256(0x40)
        threshold_enc = _tron_abi_encode_uint256(threshold)
        length_enc = _tron_abi_encode_uint256(n)
        owners_enc = "".join(_tron_abi_encode_address(addr) for addr in owners)
        return offset + threshold_enc + length_enc + owners_enc

    async def deploy_tron_contract(
        self,
        owners: list[str],
        threshold: int,
        gas_wallet_address: str,
        gas_wallet_private_key: str,
        rental_api_url: str,
        rental_api_key: str,
        rental_max_price_sun: int = 420,
    ) -> tuple[str, str]:
        """
        部署 TronMultiSig 合约到 TRON 主网。

        流程：
        1. 强制从 feee.io 租赁 TRON_DEPLOY_ENERGY 能量（降低部署费用）
        2. 调用 /wallet/deploycontract 创建部署交易
        3. 用 gas 钱包私钥签名并广播
        4. 轮询确认，提取合约地址

        返回: (contract_address_base58, tx_hash)
        """
        settings = await self._load_settings()
        api_urls = settings.tron_api_urls or []
        api_keys = settings.tron_api_keys or []

        # 1. 强制租能量
        logger.info("部署 TronMultiSig 前租赁能量: 目标 %d energy, gas 钱包 %s",
                    TRON_DEPLOY_ENERGY, gas_wallet_address[:10])
        energy_result = await tron_energy_service.ensure_energy(
            api_urls=api_urls,
            api_keys=api_keys,
            address=gas_wallet_address,
            energy_needed=TRON_DEPLOY_ENERGY,
            rental_enabled=True,
            rental_api_url=rental_api_url,
            rental_api_key=rental_api_key,
            rental_max_price_sun=rental_max_price_sun,
            rental_duration_ms=3_600_000,
        )
        if not energy_result.get("sufficient"):
            err = energy_result.get("error", "未知错误")
            raise RuntimeError(f"部署能量不足，无法继续: {err}")
        logger.info("能量就绪: available=%d, needed=%d",
                    energy_result["available"], energy_result["needed"])

        # 2. 构造部署 payload
        constructor_params = self._abi_encode_constructor(owners, threshold)
        abi_json = json.dumps(TRON_MULTISIG_ABI)

        deploy_payload = {
            "owner_address": gas_wallet_address,
            "bytecode": TRON_MULTISIG_BYTECODE + constructor_params,
            "abi": abi_json,
            "name": "TronMultiSig",
            "call_value": 0,
            "consume_user_resource_percent": 100,  # 全部由调用方出能量
            "origin_energy_limit": 10_000_000,
            "fee_limit": 1_000_000_000,             # 1000 TRX 上限（安全边界）
            "visible": True,
        }

        data = await self._tron_api_call(
            api_urls, api_keys, "/wallet/deploycontract", deploy_payload,
        )
        if data.get("Error") or data.get("error"):
            raise RuntimeError(f"deploycontract 失败: {data}")

        raw_data_hex = data.get("raw_data_hex", "")
        if not raw_data_hex:
            raise RuntimeError(f"deploycontract 返回缺少 raw_data_hex: {data}")

        # 3. 签名 + 广播
        sig = _tron_sign_transaction(raw_data_hex, gas_wallet_private_key)
        data["signature"] = [sig]

        broadcast = await self._tron_api_call(
            api_urls, api_keys, "/wallet/broadcasttransaction", data,
        )
        if not broadcast.get("result"):
            raise RuntimeError(f"广播部署交易失败: {broadcast}")

        tx_hash = broadcast.get("txid") or data.get("txID", "")
        logger.info("TronMultiSig 部署交易广播成功: txid=%s", tx_hash)

        # 4. 轮询确认，获取合约地址（最多等 60s）
        contract_address = await self._wait_for_contract_address(
            api_urls, api_keys, tx_hash, timeout_s=60,
        )
        logger.info("TronMultiSig 合约部署成功: %s (tx: %s)", contract_address, tx_hash)
        return contract_address, tx_hash

    async def _wait_for_contract_address(
        self,
        api_urls: list,
        api_keys: list,
        tx_hash: str,
        timeout_s: int = 60,
    ) -> str:
        """轮询 gettransactioninfobyid 直到合约地址出现"""
        for attempt in range(timeout_s // 3):
            await asyncio.sleep(3)
            try:
                info = await self._tron_api_call(
                    api_urls, api_keys,
                    "/wallet/gettransactioninfobyid",
                    {"value": tx_hash},
                )
                # 检查是否执行失败
                receipt = info.get("receipt", {})
                if receipt.get("result") in ("FAILED", "OUT_OF_ENERGY", "REVERT"):
                    raise RuntimeError(
                        f"部署交易链上失败: receipt.result={receipt.get('result')}"
                    )
                contract_hex = info.get("contract_address", "")
                if contract_hex:
                    # TRON API 返回的 contract_address 已含 "41" 前缀（21字节）
                    # tron_hex_to_base58 期望 20字节（不含"41"），需先去掉前缀
                    clean = contract_hex.replace("0x", "")
                    if clean.startswith("41") and len(clean) == 42:
                        clean = clean[2:]  # 去掉 "41" 前缀，只保留 20 字节
                    contract_base58 = tron_hex_to_base58(clean)
                    return contract_base58
            except RuntimeError:
                raise
            except Exception as e:
                logger.debug("等待合约地址 (attempt %d): %s", attempt + 1, e)
            logger.debug("等待合约部署确认 (%d/%d)...", attempt + 1, timeout_s // 3)
        raise RuntimeError(f"合约部署超时（{timeout_s}s），txid={tx_hash}")

    async def verify_tron_contract(self, contract_address: str) -> dict:
        """
        链上验证 TronMultiSig 合约，读取 owners 和 threshold。
        通过 triggersmartcontract 调用 getOwners() 和 threshold()。
        返回: {"owners": [...], "threshold": int, "contract": True}
        """
        settings = await self._load_settings()
        api_urls = settings.tron_api_urls or []
        api_keys = settings.tron_api_keys or []

        # 调用 getOwners()
        owners_result = await self._tron_api_call(
            api_urls, api_keys,
            "/wallet/triggersmartcontract",
            {
                "owner_address": contract_address,
                "contract_address": contract_address,
                "function_selector": "getOwners()",
                "parameter": "",
                "call_value": 0,
                "visible": True,
            },
        )
        # 调用 threshold()
        threshold_result = await self._tron_api_call(
            api_urls, api_keys,
            "/wallet/triggersmartcontract",
            {
                "owner_address": contract_address,
                "contract_address": contract_address,
                "function_selector": "threshold()",
                "parameter": "",
                "call_value": 0,
                "visible": True,
            },
        )

        # 解析 threshold（32 字节 hex → int）
        threshold_hex = (threshold_result.get("constant_result") or ["0" * 64])[0]
        threshold = int(threshold_hex, 16) if threshold_hex else 0

        # 解析 getOwners() 返回的 address[]
        # ABI 编码: offset(32) + length(32) + owner0(32) + owner1(32) ...
        owners_hex = (owners_result.get("constant_result") or [""])[0]
        owners = []
        if len(owners_hex) >= 128:
            # 跳过 offset (64 chars), 读 length (64 chars)
            n = int(owners_hex[64:128], 16)
            for i in range(n):
                start = 128 + i * 64
                addr_hex = owners_hex[start + 24: start + 64]  # 取后 20 bytes
                owners.append(tron_hex_to_base58(addr_hex))

        return {"owners": owners, "threshold": threshold, "contract": True}

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRON 原生多签（保留，用于 import 已有原生多签钱包的验证）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _tron_api_call(self, api_urls, api_keys, path, payload):
        """复用 chain_client 的 TRON API 调用模式"""
        import httpx
        for i, api_url in enumerate(api_urls or []):
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_keys:
                headers["TRON-PRO-API-KEY"] = api_keys[i % len(api_keys)]
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"{api_url}{path}", json=payload, headers=headers
                    )
                    if resp.status_code != 200:
                        continue
                    return resp.json()
            except Exception as e:
                logger.debug("TRON API %s%s 失败: %s", api_url[:30], path, e)
                continue
        raise RuntimeError(f"TRON 所有 API 节点 {path} 调用失败")

    async def setup_tron_multisig(
        self,
        account_address: str,
        account_private_key: str,
        owners: list[str],
        threshold: int,
    ) -> str:
        """
        设置 TRON 账户为多签。

        通过 AccountPermissionUpdate 更新 owner_permission 和 active_permission。
        费用约 100 TRX。

        返回: tx_hash
        """
        settings = await self._load_settings()

        # 构建权限更新 payload
        owner_keys = [{"address": addr, "weight": 1} for addr in owners]
        active_keys = [{"address": addr, "weight": 1} for addr in owners]

        payload = {
            "owner_address": account_address,
            "owner": {
                "type": 0,
                "permission_name": "owner",
                "threshold": threshold,
                "keys": owner_keys,
            },
            "actives": [
                {
                    "type": 2,
                    "permission_name": "active0",
                    "threshold": threshold,
                    "operations": TRON_ACTIVE_OPERATIONS,
                    "keys": active_keys,
                }
            ],
            "visible": True,
        }

        # 创建交易
        data = await self._tron_api_call(
            settings.tron_api_urls, settings.tron_api_keys,
            "/wallet/accountpermissionupdate", payload,
        )

        if "Error" in str(data) or data.get("error"):
            raise RuntimeError(f"TRON AccountPermissionUpdate 失败: {data}")

        raw_data_hex = data.get("raw_data_hex", "")
        if not raw_data_hex:
            raise RuntimeError(f"TRON 交易缺少 raw_data_hex: {data}")

        # 签名
        sig = _tron_sign_transaction(raw_data_hex, account_private_key)
        data["signature"] = [sig]

        # 广播
        result = await self._tron_api_call(
            settings.tron_api_urls, settings.tron_api_keys,
            "/wallet/broadcasttransaction", data,
        )
        if not result.get("result"):
            raise RuntimeError(f"TRON 广播失败: {result}")

        tx_hash = result.get("txid", data.get("txID", ""))
        logger.info("TRON 多签设置成功: %s (tx: %s)", account_address[:10], tx_hash)
        return tx_hash

    async def verify_tron_multisig(self, address: str) -> dict:
        """
        链上验证 TRON 多签账户，读取 active_permission。

        返回: {"owners": [...], "threshold": int}
        """
        settings = await self._load_settings()

        data = await self._tron_api_call(
            settings.tron_api_urls, settings.tron_api_keys,
            "/wallet/getaccount", {"address": address, "visible": True},
        )

        # 优先检查 active_permission
        active_perms = data.get("active_permission", [])
        if active_perms:
            perm = active_perms[0]
            keys = perm.get("keys", [])
            if len(keys) >= 2:
                owners = [k["address"] for k in keys]
                return {
                    "owners": owners,
                    "threshold": perm.get("threshold", 1),
                }

        # 检查 owner_permission
        owner_perm = data.get("owner_permission", {})
        owner_keys = owner_perm.get("keys", [])
        if len(owner_keys) >= 2:
            owners = [k["address"] for k in owner_keys]
            return {
                "owners": owners,
                "threshold": owner_perm.get("threshold", 1),
            }

        raise RuntimeError("该 TRON 账户不是多签账户")


# ─── 模块级单例 ──────────────────────────────────────

multisig_service = MultisigService()
