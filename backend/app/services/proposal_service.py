"""
提案服务 — BSC Safe / TRON 多签交易构建、签名验证、执行

BSC: EIP-712 SafeTxHash + execTransaction
TRON: triggersmartcontract + 多签名广播
"""

import asyncio
import hashlib
import logging
from decimal import Decimal

from eth_abi import encode as abi_encode
from eth_account.messages import defunct_hash_message
from eth_keys import keys as eth_keys
from web3 import Web3
from eth_account import Account

import base58
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.system_settings import SystemSettings
from app.services.safe_constants import SAFE_ABI
from app.services.chain_client import (
    tron_base58_to_hex, tron_hex_to_base58,
    _tron_abi_encode_address, _tron_abi_encode_uint256,
    _tron_sign_transaction,
    ERC20_ABI, BSC_CHAIN_ID,
)
from app.services.tron_energy import tron_energy_service

# 多签合约 execute() 消耗能量约 180,000~210,000（含内部 USDT transfer，实测 ~200k）
TRON_MULTISIG_EXECUTE_ENERGY = 200_000

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


# ─── Protobuf varint 辅助函数 ────────────────────────────

def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """解码 protobuf varint，返回 (value, new_offset)"""
    val = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        val |= (b & 0x7F) << shift
        shift += 7
        offset += 1
        if not (b & 0x80):
            break
    return val, offset


def _encode_varint(value: int) -> bytes:
    """编码 protobuf varint"""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _replace_protobuf_varint_field(data: bytes, field_number: int, new_value: int) -> bytes:
    """
    在 protobuf 编码的 data 中找到指定 field_number 的 varint 字段，替换其值。
    返回新的 bytes。
    """
    result = bytearray()
    i = 0
    while i < len(data):
        tag_start = i
        tag, i = _decode_varint(data, i)
        fn = tag >> 3
        wt = tag & 0x07

        if wt == 0:  # varint
            _, new_i = _decode_varint(data, i)
            if fn == field_number:
                # 替换这个字段
                result.extend(_encode_varint(tag))
                result.extend(_encode_varint(new_value))
            else:
                result.extend(data[tag_start:new_i])
            i = new_i
        elif wt == 2:  # length-delimited
            length, i = _decode_varint(data, i)
            end = i + length
            result.extend(data[tag_start:end])
            i = end
        elif wt == 5:  # 32-bit
            result.extend(data[tag_start:i + 4])
            i += 4
        elif wt == 1:  # 64-bit
            result.extend(data[tag_start:i + 8])
            i += 8
        else:
            # 未知类型，保留剩余数据
            result.extend(data[tag_start:])
            break

    return bytes(result)


# ─── EIP-712 Constants ────────────────────────────────

DOMAIN_SEPARATOR_TYPEHASH = Web3.keccak(
    text="EIP712Domain(uint256 chainId,address verifyingContract)"
)

SAFE_TX_TYPEHASH = Web3.keccak(
    text="SafeTx(address to,uint256 value,bytes data,uint8 operation,"
         "uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,"
         "address gasToken,address refundReceiver,uint256 nonce)"
)


class ProposalService:

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
    # BSC Safe 交易
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def build_bsc_safe_tx(
        self,
        safe_address: str,
        to_address: str,
        amount: Decimal,
        usdt_contract: str,
    ) -> dict:
        """
        构建 BSC Safe USDT 转账交易数据。

        返回 SafeTransaction 参数字典。
        """
        settings = await self._load_settings()

        def _build():
            for rpc_url in (settings.bsc_rpc_urls or []):
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

                    # 编码 ERC20.transfer(to, amount)
                    usdt = w3.eth.contract(
                        address=Web3.to_checksum_address(usdt_contract),
                        abi=ERC20_ABI,
                    )
                    # BSC USDT 是 18 位小数
                    amount_wei = int(amount * Decimal(10 ** 18))
                    transfer_data = usdt.encodeABI(
                        fn_name="transfer",
                        args=[Web3.to_checksum_address(to_address), amount_wei],
                    )

                    # 获取 Safe nonce
                    safe = w3.eth.contract(
                        address=Web3.to_checksum_address(safe_address),
                        abi=SAFE_ABI,
                    )
                    nonce = safe.functions.nonce().call()

                    return {
                        "to": Web3.to_checksum_address(usdt_contract),
                        "value": 0,
                        "data": transfer_data,
                        "operation": 0,  # Call
                        "safeTxGas": 0,
                        "baseGas": 0,
                        "gasPrice": 0,
                        "gasToken": ZERO_ADDRESS,
                        "refundReceiver": ZERO_ADDRESS,
                        "nonce": nonce,
                        # 额外信息供前端显示
                        "_to_address": to_address,
                        "_amount": str(amount),
                        "_amount_wei": str(amount_wei),
                    }
                except Exception as e:
                    logger.warning("build_bsc_safe_tx via %s 失败: %s", rpc_url[:30], e)
                    continue
            raise RuntimeError("BSC Safe 交易构建失败：所有 RPC 不可用")

        return await asyncio.to_thread(_build)

    async def build_bsc_safe_multisend_tx(
        self,
        safe_address: str,
        items: list,
        asset_type: str,
        usdt_contract: str,
    ) -> dict:
        """构建 BSC Safe MultiSend 批量转账，一次 Safe tx 打给 N 个地址（DelegateCall）"""
        settings = await self._load_settings()
        BSC_MULTISEND = "0x40A2aCCbd92BCA938b02010E17A5b8929b49130D"
        MULTISEND_ABI = [{"name": "multiSend", "type": "function", "inputs": [{"name": "transactions", "type": "bytes"}], "outputs": [], "stateMutability": "nonpayable"}]

        def _build():
            for rpc_url in (settings.bsc_rpc_urls or []):
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
                    usdt = w3.eth.contract(address=Web3.to_checksum_address(usdt_contract), abi=ERC20_ABI)

                    packed = b""
                    for item in items:
                        if asset_type == "usdt":
                            amount_wei = int(Decimal(str(item.amount)) * Decimal(10 ** 18))
                            call_data = bytes.fromhex(usdt.encodeABI("transfer", [Web3.to_checksum_address(item.to_address), amount_wei])[2:])
                            to_bytes = bytes.fromhex(Web3.to_checksum_address(usdt_contract)[2:])
                            value = 0
                        else:
                            amount_wei = int(Decimal(str(item.amount)) * Decimal(10 ** 18))
                            call_data = b""
                            to_bytes = bytes.fromhex(Web3.to_checksum_address(item.to_address)[2:])
                            value = amount_wei
                        # op(1) + to(20) + value(32) + dataLen(32) + data
                        packed += b'\x00' + to_bytes + value.to_bytes(32, 'big') + len(call_data).to_bytes(32, 'big') + call_data

                    ms = w3.eth.contract(address=Web3.to_checksum_address(BSC_MULTISEND), abi=MULTISEND_ABI)
                    ms_data = ms.encodeABI("multiSend", args=[packed])

                    safe = w3.eth.contract(address=Web3.to_checksum_address(safe_address), abi=SAFE_ABI)
                    nonce = safe.functions.nonce().call()

                    return {
                        "to": Web3.to_checksum_address(BSC_MULTISEND),
                        "value": 0,
                        "data": ms_data,
                        "operation": 1,  # DelegateCall
                        "safeTxGas": 0,
                        "baseGas": 0,
                        "gasPrice": 0,
                        "gasToken": ZERO_ADDRESS,
                        "refundReceiver": ZERO_ADDRESS,
                        "nonce": nonce,
                    }
                except Exception as e:
                    logger.warning("build_bsc_safe_multisend_tx via %s 失败: %s", rpc_url[:30], e)
                    continue
            raise RuntimeError("BSC Safe MultiSend 构建失败：所有 RPC 不可用")

        return await asyncio.to_thread(_build)

    async def build_bsc_safe_native_tx(
        self,
        safe_address: str,
        to_address: str,
        amount: Decimal,
    ) -> dict:
        """
        构建 BSC Safe 原生 BNB 转账交易数据。

        与 USDT 不同: to=收款地址, value=amount, data=空
        """
        settings = await self._load_settings()

        def _build():
            for rpc_url in (settings.bsc_rpc_urls or []):
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

                    # BNB 18 位小数
                    amount_wei = int(amount * Decimal(10 ** 18))

                    # 获取 Safe nonce
                    safe = w3.eth.contract(
                        address=Web3.to_checksum_address(safe_address),
                        abi=SAFE_ABI,
                    )
                    nonce = safe.functions.nonce().call()

                    return {
                        "to": Web3.to_checksum_address(to_address),
                        "value": amount_wei,
                        "data": "0x",
                        "operation": 0,  # Call
                        "safeTxGas": 0,
                        "baseGas": 0,
                        "gasPrice": 0,
                        "gasToken": ZERO_ADDRESS,
                        "refundReceiver": ZERO_ADDRESS,
                        "nonce": nonce,
                        # 额外信息
                        "_to_address": to_address,
                        "_amount": str(amount),
                        "_amount_wei": str(amount_wei),
                        "_token": "native",
                    }
                except Exception as e:
                    logger.warning("build_bsc_safe_native_tx via %s 失败: %s", rpc_url[:30], e)
                    continue
            raise RuntimeError("BSC Safe 原生转账交易构建失败：所有 RPC 不可用")

        return await asyncio.to_thread(_build)

    def compute_safe_tx_hash(
        self,
        safe_address: str,
        tx_data: dict,
        chain_id: int = BSC_CHAIN_ID,
    ) -> str:
        """
        计算 EIP-712 safeTxHash。

        Safe v1.3.0 规范：
        safeTxHash = keccak256(0x19 || 0x01 || domainSeparator || safeTxStructHash)
        """
        # Domain separator
        domain_separator = Web3.keccak(
            abi_encode(
                ["bytes32", "uint256", "address"],
                [
                    DOMAIN_SEPARATOR_TYPEHASH,
                    chain_id,
                    Web3.to_checksum_address(safe_address),
                ],
            )
        )

        # data hash
        data_bytes = bytes.fromhex(tx_data["data"][2:]) if tx_data["data"].startswith("0x") else bytes.fromhex(tx_data["data"])

        # Struct hash
        struct_hash = Web3.keccak(
            abi_encode(
                [
                    "bytes32", "address", "uint256", "bytes32", "uint8",
                    "uint256", "uint256", "uint256", "address", "address", "uint256",
                ],
                [
                    SAFE_TX_TYPEHASH,
                    Web3.to_checksum_address(tx_data["to"]),
                    tx_data["value"],
                    Web3.keccak(data_bytes),
                    tx_data["operation"],
                    tx_data["safeTxGas"],
                    tx_data["baseGas"],
                    tx_data["gasPrice"],
                    Web3.to_checksum_address(tx_data["gasToken"]),
                    Web3.to_checksum_address(tx_data["refundReceiver"]),
                    tx_data["nonce"],
                ],
            )
        )

        # Final hash: 0x19 0x01 domainSeparator structHash
        safe_tx_hash = Web3.keccak(
            b"\x19\x01" + domain_separator + struct_hash
        )

        return safe_tx_hash.hex()

    def verify_bsc_signature(
        self,
        safe_tx_hash: str,
        signature: str,
        expected_signer: str,
    ) -> bool:
        """
        验证 BSC 签名。
        支持三种模式（按优先级尝试）：
        1. EIP-712 signTypedData (v=27/28): 直接从 safeTxHash 恢复，无前缀
        2. personal_sign / eth_sign (v=27/28): 加 "\\x19Ethereum Signed Message:\\n32" 前缀
        3. Safe eth_sign 模式 (v>=31): v -= 4 后同 personal_sign
        """
        try:
            sig_bytes = bytes.fromhex(signature.replace("0x", ""))
            if len(sig_bytes) != 65:
                return False

            r = int.from_bytes(sig_bytes[:32], "big")
            s = int.from_bytes(sig_bytes[32:64], "big")
            v = sig_bytes[64]

            hash_bytes = bytes.fromhex(safe_tx_hash.replace("0x", ""))
            expected = expected_signer.lower()

            # 1) EIP-712 signTypedData: 直接从 safeTxHash 恢复（无前缀）
            if v in (27, 28):
                try:
                    v_norm = v - 27
                    pk = eth_keys.Signature(vrs=(v_norm, r, s))
                    recovered = pk.recover_public_key_from_msg_hash(hash_bytes).to_checksum_address()
                    if recovered.lower() == expected:
                        return True
                except Exception:
                    pass

            # 2) personal_sign: 加 Ethereum Signed Message 前缀
            prefixed = b"\x19Ethereum Signed Message:\n32" + hash_bytes
            msg_hash_prefixed = Web3.keccak(prefixed)

            if v >= 31:
                # Safe eth_sign 模式: v -= 4
                v_norm = v - 4 - 27
            else:
                v_norm = v - 27

            try:
                pk = eth_keys.Signature(vrs=(v_norm, r, s))
                recovered = pk.recover_public_key_from_msg_hash(msg_hash_prefixed).to_checksum_address()
                if recovered.lower() == expected:
                    return True
            except Exception:
                pass

            logger.warning("BSC 签名验证: 所有模式均未匹配 signer=%s", expected_signer)
            return False
        except Exception as e:
            logger.warning("BSC 签名验证失败: %s", e)
            return False

    async def execute_bsc_safe_tx(
        self,
        safe_address: str,
        tx_data: dict,
        signatures: list[tuple[str, str]],
        gas_private_key: str,
    ) -> str:
        """
        执行 BSC Safe 交易。

        signatures: [(signer_address, signature_hex), ...]
        """
        settings = await self._load_settings()

        def _execute():
            # 按地址升序排列（Safe 要求）
            sorted_sigs = sorted(signatures, key=lambda x: x[0].lower())

            # 拼接签名 bytes
            packed = b""
            for _, sig_hex in sorted_sigs:
                sig_bytes = bytes.fromhex(sig_hex.replace("0x", ""))
                packed += sig_bytes

            if not gas_private_key.startswith("0x"):
                key = "0x" + gas_private_key
            else:
                key = gas_private_key
            sender = Account.from_key(key)

            for rpc_url in (settings.bsc_rpc_urls or []):
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))

                    safe = w3.eth.contract(
                        address=Web3.to_checksum_address(safe_address),
                        abi=SAFE_ABI,
                    )

                    data_bytes = bytes.fromhex(tx_data["data"][2:]) if tx_data["data"].startswith("0x") else bytes.fromhex(tx_data["data"])

                    gas_price = int(w3.eth.gas_price * 1.1)
                    tx = safe.functions.execTransaction(
                        Web3.to_checksum_address(tx_data["to"]),
                        tx_data["value"],
                        data_bytes,
                        tx_data["operation"],
                        tx_data["safeTxGas"],
                        tx_data["baseGas"],
                        tx_data["gasPrice"],
                        Web3.to_checksum_address(tx_data["gasToken"]),
                        Web3.to_checksum_address(tx_data["refundReceiver"]),
                        packed,
                    ).build_transaction({
                        "from": sender.address,
                        "gas": 500_000,
                        "gasPrice": gas_price,
                        "nonce": w3.eth.get_transaction_count(sender.address),
                        "chainId": BSC_CHAIN_ID,
                    })

                    signed = sender.sign_transaction(tx)
                    raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
                    tx_hash = w3.eth.send_raw_transaction(raw_tx)
                    tx_hash_hex = tx_hash.hex()

                    # 不等待回执（避免前端超时）：发送成功即返回 tx_hash
                    # 链上失败的概率极低（签名已验证），后台异步检查
                    logger.info("BSC Safe 交易已广播: %s", tx_hash_hex)
                    return tx_hash_hex

                except Exception as e:
                    logger.warning("execute_bsc_safe_tx via %s 失败: %s", rpc_url[:30], e)
                    continue

            raise RuntimeError("BSC Safe 交易执行失败：所有 RPC 不可用")

        return await asyncio.to_thread(_execute)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRON 多签交易
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _tron_api_call(self, api_urls, api_keys, path, payload):
        """复用 TRON API 调用模式"""
        import httpx
        for i, api_url in enumerate(api_urls or []):
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_keys:
                headers["TRON-PRO-API-KEY"] = api_keys[i % len(api_keys)]
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"{api_url}{path}", json=payload, headers=headers,
                    )
                    if resp.status_code != 200:
                        continue
                    return resp.json()
            except Exception as e:
                logger.debug("TRON API %s%s 失败: %s", api_url[:30], path, e)
                continue
        raise RuntimeError(f"TRON 所有 API 节点 {path} 调用失败")

    async def build_tron_multisig_tx(
        self,
        owner_address: str,
        to_address: str,
        amount: Decimal,
        usdt_contract: str,
    ) -> dict:
        """
        构建 TRON 多签 USDT 转账交易。

        使用 triggersmartcontract + permission_id=2 (active permission)。
        返回完整交易数据。
        """
        settings = await self._load_settings()

        # ABI 编码 transfer(address, uint256)
        to_hex = _tron_abi_encode_address(to_address)
        amount_sun = int(amount * Decimal(10 ** 6))  # TRC20 USDT 6位小数
        amount_hex = _tron_abi_encode_uint256(amount_sun)
        parameter = to_hex + amount_hex

        payload = {
            "owner_address": owner_address,
            "contract_address": usdt_contract,
            "function_selector": "transfer(address,uint256)",
            "parameter": parameter,
            "fee_limit": 100_000_000,  # 100 TRX
            "call_value": 0,
            # 不传 permission_id，使用默认 Owner Permission（id=0）
            # Active Permission (id=2) 会导致 TronLink multiSign 地址比对失败
            "visible": True,
        }

        data = await self._tron_api_call(
            settings.tron_api_urls, settings.tron_api_keys,
            "/wallet/triggersmartcontract", payload,
        )

        if data.get("result", {}).get("result") is not True:
            error_msg = data.get("result", {}).get("message", "")
            if error_msg:
                error_msg = bytes.fromhex(error_msg).decode("utf-8", errors="ignore")
            raise RuntimeError(f"TRON 交易构建失败: {error_msg or data}")

        transaction = data.get("transaction", {})
        if not transaction.get("raw_data_hex"):
            raise RuntimeError(f"TRON 交易缺少 raw_data_hex: {data}")

        # 延长交易过期时间到 24 小时（默认仅 60 秒，多签审批需要更长）
        transaction = self._extend_tron_tx_expiration(transaction, hours=24)

        # 多签 USDT 转账实测能量消耗约 75,000~76,000，固定用 85,000 做安全上限
        # triggerconstantcontract 对多签钱包会返回偏高值（含权限验证开销），不可靠，不使用
        estimated_energy = 85_000

        return {
            "transaction": transaction,
            "raw_data_hex": transaction["raw_data_hex"],
            "txID": transaction.get("txID", ""),
            # 额外信息
            "_to_address": to_address,
            "_amount": str(amount),
            "_amount_sun": str(amount_sun),
            "_estimated_energy": estimated_energy,  # 精确能量估算，供执行时租赁使用
        }

    async def build_tron_multisig_native_tx(
        self,
        owner_address: str,
        to_address: str,
        amount: Decimal,
    ) -> dict:
        """
        构建 TRON 多签原生 TRX 转账交易。

        使用 createtransaction + permission_id=2 (active permission)。
        """
        settings = await self._load_settings()

        # TRX 6 位精度 (1 TRX = 1,000,000 SUN)
        amount_sun = int(amount * Decimal(10 ** 6))

        payload = {
            "to_address": to_address,
            "owner_address": owner_address,
            "amount": amount_sun,
            # 不传 permission_id，使用默认 Owner Permission（id=0）
            "visible": True,
        }

        data = await self._tron_api_call(
            settings.tron_api_urls, settings.tron_api_keys,
            "/wallet/createtransaction", payload,
        )

        if not data.get("raw_data_hex"):
            raise RuntimeError(f"TRON 原生转账构建失败: {data}")

        # 延长交易过期时间到 24 小时
        data = self._extend_tron_tx_expiration(data, hours=24)

        return {
            "transaction": data,
            "raw_data_hex": data["raw_data_hex"],
            "txID": data.get("txID", ""),
            # 额外信息
            "_to_address": to_address,
            "_amount": str(amount),
            "_amount_sun": str(amount_sun),
            "_token": "native",
        }

    @staticmethod
    def _extend_tron_tx_expiration(transaction: dict, hours: int = 24) -> dict:
        """
        延长 TRON 交易的过期时间。

        TRON 默认交易过期 60 秒，多签审批需要更长时间。
        修改 raw_data.expiration，重新编码 raw_data_hex 和 txID。

        TRON raw_data 是 protobuf 编码，expiration 是 field 8 (int64)。
        我们解码整个 raw_data_hex，修改 expiration，重新编码。
        """
        import time

        raw_hex = transaction.get("raw_data_hex", "")
        if not raw_hex:
            return transaction

        raw_bytes = bytes.fromhex(raw_hex)
        new_expiration = int(time.time() * 1000) + hours * 3600 * 1000

        # 解析 protobuf wire format，找到并替换 field 8 (expiration)
        new_raw = _replace_protobuf_varint_field(raw_bytes, field_number=8, new_value=new_expiration)

        transaction = transaction.copy()
        transaction["raw_data_hex"] = new_raw.hex()
        transaction["txID"] = hashlib.sha256(new_raw).hexdigest()
        # 同步更新 raw_data JSON
        if "raw_data" in transaction:
            transaction["raw_data"] = transaction["raw_data"].copy()
            transaction["raw_data"]["expiration"] = new_expiration

        return transaction

    def compute_tron_tx_hash(self, raw_data_hex: str) -> str:
        """SHA256(raw_data_hex)"""
        raw_bytes = bytes.fromhex(raw_data_hex)
        return hashlib.sha256(raw_bytes).hexdigest()

    @staticmethod
    def _unwrap_tron_raw_data(data: bytes) -> bytes:
        """
        TronLink 手机版 signedTx.raw_data_hex 有时返回外层 Transaction 序列化
        格式: 0x0a [varint_len] [inner_raw_data_bytes]
        正常 raw_data 的 field 1 (ref_block_bytes) 固定 2 字节，第二字节是 0x02。
        如果解析出来 header+length == 总长度 且 length > 10，判定为外层包装并剥除。
        """
        if len(data) < 4 or data[0] != 0x0a:
            return data
        idx = 1
        length = 0
        shift = 0
        for _ in range(5):
            if idx >= len(data):
                return data
            b = data[idx]; idx += 1
            length |= (b & 0x7f) << shift
            shift += 7
            if not (b & 0x80):
                break
        if idx + length == len(data) and length > 10:
            logger.info("TRON raw_data_hex 检测到外层 Transaction 包装，剥除 %d 字节 header", idx)
            return data[idx: idx + length]
        return data

    def recover_tron_signer(
        self,
        raw_data_hex: str,
        signature: str,
    ) -> "str | None":
        """从 TRON 交易签名中恢复签名人地址。返回 base58 地址，失败返回 None。"""
        try:
            original_bytes = bytes.fromhex(raw_data_hex)
            raw_bytes = self._unwrap_tron_raw_data(original_bytes)
            logger.info("[recover] raw_data len=%d after_unwrap len=%d first10=%s sig_prefix=%s",
                        len(original_bytes), len(raw_bytes),
                        raw_data_hex[:20], signature[:16])
            msg_hash = hashlib.sha256(raw_bytes).digest()
            logger.info("[recover] msg_hash=%s", msg_hash.hex())

            sig_bytes = bytes.fromhex(signature.replace("0x", ""))
            if len(sig_bytes) != 65:
                return None

            r = int.from_bytes(sig_bytes[:32], "big")
            s = int.from_bytes(sig_bytes[32:64], "big")
            v = sig_bytes[64]
            if v >= 27:
                v -= 27

            sig_obj = eth_keys.Signature(vrs=(v, r, s))
            recovered_key = sig_obj.recover_public_key_from_msg_hash(msg_hash)
            pub_bytes = recovered_key.to_bytes()
            addr_hash = Web3.keccak(pub_bytes)[-20:]
            return tron_hex_to_base58(addr_hash.hex())
        except Exception as e:
            logger.warning("TRON 签名恢复失败: %s", e)
            return None

    def verify_tron_signature(
        self,
        raw_data_hex: str,
        signature: str,
        expected_signer: str,
    ) -> bool:
        """
        验证 TRON 签名。

        1. SHA256(raw_data) 得到消息哈希
        2. 从签名恢复公钥 → 地址
        3. 比对 TRON 地址
        """
        recovered_addr = self.recover_tron_signer(raw_data_hex, signature)
        if recovered_addr is None:
            return False
        match = recovered_addr == expected_signer
        if not match:
            logger.warning(
                "TRON 签名验证失败: recovered=%s expected=%s",
                recovered_addr, expected_signer,
            )
        return match

    def verify_collection_signature_tron(
        self,
        collection_hash: str,
        signature: str,
        expected_signer: str,
    ) -> bool:
        """
        验证 TRON 归集提案签名。

        TronLink signMessageV2(msg) 签名流程:
        1. 将 msg 完整字符串视为 UTF-8（含 "0x" 前缀，共 66 字节）
        2. message_bytes = msg.encode('utf-8')  # "0x" + 64 hex chars = 66 bytes
        3. prefixed = "\\x19TRON Signed Message:\\n66" + message_bytes
        4. hash = keccak256(prefixed)
        5. sign(hash)
        """
        try:
            # TronLink signMessageV2 将完整字符串视为 UTF-8（含 "0x" 前缀共 66 字节）
            # 例: "0xa9295bad...02e" (66 chars)，前缀 "\x19TRON Signed Message:\n66"
            msg_bytes = collection_hash.encode("utf-8")
            prefix = f"\x19TRON Signed Message:\n{len(msg_bytes)}".encode("utf-8")
            prefixed = prefix + msg_bytes
            msg_hash = Web3.keccak(prefixed)

            sig_bytes = bytes.fromhex(signature.replace("0x", ""))
            if len(sig_bytes) != 65:
                return False

            r = int.from_bytes(sig_bytes[:32], "big")
            s = int.from_bytes(sig_bytes[32:64], "big")
            v = sig_bytes[64]

            if v >= 27:
                v -= 27

            sig_obj = eth_keys.Signature(vrs=(v, r, s))
            recovered_key = sig_obj.recover_public_key_from_msg_hash(msg_hash)

            pub_bytes = recovered_key.to_bytes()
            addr_hash = Web3.keccak(pub_bytes)[-20:]
            recovered_addr = tron_hex_to_base58(addr_hash.hex())

            logger.info("TRON 签名验证: recovered=%s, expected=%s", recovered_addr, expected_signer)
            return recovered_addr == expected_signer
        except Exception as e:
            logger.warning("TRON 归集签名验证失败: %s", e)
            return False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRON 合约多签（TronMultiSig.sol）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def build_tron_contract_proposal(
        self,
        contract_address: str,
        token_address: str,
        to_address: str,
        amount: Decimal,
    ) -> dict:
        """
        获取合约多签提案的消息哈希（无需构建链上交易）。

        1. 读取合约当前 nonce
        2. 调用 getMessageHash(token, to, amount, nonce) 获取 bytes32
        3. 返回 {"msg_hash": "0x...", "nonce": int, ...}

        前端用 signMessageV2(msg_hash) 签名，与归集提案完全相同。
        """
        settings = await self._load_settings()
        api_urls = settings.tron_api_urls or []
        api_keys = settings.tron_api_keys or []

        amount_sun = int(amount * Decimal(10 ** 6))
        token_hex = "000000000000000000000000" + tron_base58_to_hex(token_address)
        to_hex = "000000000000000000000000" + tron_base58_to_hex(to_address)

        # 1. 读取 nonce
        nonce_result = await self._tron_api_call(
            api_urls, api_keys,
            "/wallet/triggersmartcontract",
            {
                "owner_address": contract_address,
                "contract_address": contract_address,
                "function_selector": "nonce()",
                "parameter": "",
                "call_value": 0,
                "visible": True,
            },
        )
        nonce_hex = (nonce_result.get("constant_result") or ["0" * 64])[0]
        nonce = int(nonce_hex, 16) if nonce_hex else 0

        # 2. 调用 getMessageHash(token, to, amount, nonce)
        param = (
            token_hex
            + to_hex
            + _tron_abi_encode_uint256(amount_sun)
            + _tron_abi_encode_uint256(nonce)
        )
        hash_result = await self._tron_api_call(
            api_urls, api_keys,
            "/wallet/triggersmartcontract",
            {
                "owner_address": contract_address,
                "contract_address": contract_address,
                "function_selector": "getMessageHash(address,address,uint256,uint256)",
                "parameter": param,
                "call_value": 0,
                "visible": True,
            },
        )
        hash_hex = (hash_result.get("constant_result") or ["0" * 64])[0]
        msg_hash = "0x" + hash_hex  # "0x" + 64 hex chars = 66 bytes，signMessageV2 签名格式

        logger.info(
            "合约多签消息哈希: contract=%s nonce=%d amount=%d msg_hash=%s",
            contract_address[:10], nonce, amount_sun, msg_hash[:20],
        )

        return {
            "msg_hash": msg_hash,
            "nonce": nonce,
            "_contract_multisig": True,
            "_contract_address": contract_address,
            "_token_address": token_address,
            "_to_address": to_address,
            "_amount": str(amount),
            "_amount_sun": str(amount_sun),
        }

    async def execute_tron_contract_tx(
        self,
        contract_address: str,
        token_address: str,
        to_address: str,
        amount: Decimal,
        nonce: int,
        signatures: list[tuple[str, str]],  # [(signer_address, signature_hex), ...]
        gas_wallet_address: str,
        gas_wallet_private_key: str,
    ) -> str:
        """
        执行合约多签转账：调用 execute(token, to, amount, signatures[])。

        签名按 signer address 升序排列（合约要求防重放）。
        使用 gas 钱包的私钥签名并广播这笔调用交易。
        返回 tx_hash。
        """
        from eth_abi import encode as abi_encode

        settings = await self._load_settings()
        api_urls = settings.tron_api_urls or []
        api_keys = settings.tron_api_keys or []

        amount_sun = int(amount * Decimal(10 ** 6))

        # 签名按 signer address 升序排列（合约要求）
        sorted_sigs = sorted(signatures, key=lambda x: x[0].lower())
        sig_bytes_list = [bytes.fromhex(sig.replace("0x", "")) for _, sig in sorted_sigs]

        # ABI 编码 execute(address token, address to, uint256 amount, bytes[] signatures)
        # TRON 地址在 ABI 中为 20 字节 hex
        token_addr = "0x" + tron_base58_to_hex(token_address)
        to_addr = "0x" + tron_base58_to_hex(to_address)
        parameter = abi_encode(
            ["address", "address", "uint256", "bytes[]"],
            [token_addr, to_addr, amount_sun, sig_bytes_list],
        ).hex()

        payload = {
            "owner_address": gas_wallet_address,
            "contract_address": contract_address,
            "function_selector": "execute(address,address,uint256,bytes[])",
            "parameter": parameter,
            "fee_limit": 100_000_000,  # 100 TRX
            "call_value": 0,
            "visible": True,
        }

        data = await self._tron_api_call(api_urls, api_keys, "/wallet/triggersmartcontract", payload)
        if data.get("result", {}).get("result") is not True:
            error_msg = data.get("result", {}).get("message", "")
            if error_msg:
                try:
                    error_msg = bytes.fromhex(error_msg).decode("utf-8", errors="ignore")
                except Exception:
                    pass
            raise RuntimeError(f"合约 execute() 构建失败: {error_msg or data}")

        transaction = data.get("transaction", {})
        raw_data_hex = transaction.get("raw_data_hex", "")
        if not raw_data_hex:
            raise RuntimeError(f"execute() 返回缺少 raw_data_hex: {data}")

        # 用 gas 钱包签名
        sig = _tron_sign_transaction(raw_data_hex, gas_wallet_private_key)
        transaction["signature"] = [sig]

        # 广播
        broadcast = await self._tron_api_call(
            api_urls, api_keys, "/wallet/broadcasttransaction", transaction,
        )
        if not broadcast.get("result"):
            raise RuntimeError(f"合约 execute() 广播失败: {broadcast}")

        tx_hash = broadcast.get("txid") or transaction.get("txID", "")
        logger.info("合约多签 execute 成功: contract=%s tx=%s", contract_address[:10], tx_hash)
        return tx_hash

    async def execute_tron_multisig_tx(
        self,
        tx_data: dict,
        signatures: list[str],
    ) -> str:
        """
        广播 TRON 多签交易。

        将收集到的所有签名附加到交易中然后广播。
        """
        settings = await self._load_settings()

        import copy
        transaction = copy.deepcopy(tx_data["transaction"])
        transaction["signature"] = signatures

        # 使用 Owner Permission（id=0，默认），无需额外设置 Permission_id
        result = await self._tron_api_call(
            settings.tron_api_urls, settings.tron_api_keys,
            "/wallet/broadcasttransaction", transaction,
        )

        if not result.get("result"):
            raise RuntimeError(f"TRON 广播失败: {result}")

        tx_hash = result.get("txid", tx_data.get("txID", ""))
        logger.info("TRON 多签交易执行成功: %s", tx_hash)
        return tx_hash


# ─── 模块级单例 ──────────────────────────────────────

proposal_service = ProposalService()
