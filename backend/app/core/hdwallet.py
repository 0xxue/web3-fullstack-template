"""
HD Wallet 工具 — BIP-44 地址派生 (BSC / TRON)

BSC  路径: m/44'/60'/0'/0/{index}
TRON 路径: m/44'/195'/0'/0/{index}
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from eth_keys import keys as eth_keys
from eth_utils import keccak
from hdwallets import BIP32DerivationError as _  # noqa: F401 — ensure package available
from hdwallets import BIP32
from mnemonic import Mnemonic
import base58

from app.config import settings

# ─── AES-256-GCM 加解密 ────────────────────────────────

def encrypt_mnemonic(mnemonic: str, key_hex: str) -> str:
    """AES-256-GCM 加密助记词，返回 base64(nonce + ciphertext + tag)"""
    key = bytes.fromhex(key_hex)
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, mnemonic.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_mnemonic(encrypted_b64: str, key_hex: str) -> str:
    """解密 base64 密文，返回助记词明文"""
    key = bytes.fromhex(key_hex)
    raw = base64.b64decode(encrypted_b64)
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ct, None)
    return plaintext.decode("utf-8")


def generate_encryption_key() -> str:
    """生成 32 字节随机加密密钥（hex）"""
    return os.urandom(32).hex()


# ─── Seed ───────────────────────────────────────────────

_mnemo = Mnemonic("english")


def mnemonic_to_seed(mnemonic: str) -> bytes:
    return _mnemo.to_seed(mnemonic)


def _get_seed() -> bytes:
    # 优先使用加密助记词
    if settings.HD_MNEMONIC_ENCRYPTED and settings.HD_ENCRYPTION_KEY:
        mnemonic = decrypt_mnemonic(
            settings.HD_MNEMONIC_ENCRYPTED,
            settings.HD_ENCRYPTION_KEY,
        )
        return mnemonic_to_seed(mnemonic)
    # 兼容明文
    if settings.HD_MNEMONIC:
        return mnemonic_to_seed(settings.HD_MNEMONIC)
    raise RuntimeError("HD_MNEMONIC 或 HD_MNEMONIC_ENCRYPTED 未配置")


# ─── BSC (EVM) ──────────────────────────────────────────

def derive_bsc_address(seed: bytes, index: int) -> tuple[str, str]:
    """派生 BSC 地址 (EVM compatible)

    Returns: (checksum_address, private_key_hex)
    """
    bip32 = BIP32.from_seed(seed)
    private_key_bytes = bip32.get_privkey_from_path(f"m/44'/60'/0'/0/{index}")
    pk = eth_keys.PrivateKey(private_key_bytes)
    address = pk.public_key.to_checksum_address()
    return address, private_key_bytes.hex()


# ─── TRON ───────────────────────────────────────────────

def _tron_address_from_public_key(public_key_bytes: bytes) -> str:
    """公钥 → TRON 地址 (base58check with 0x41 prefix)"""
    # keccak256(uncompressed_pub_key[1:]) → 取后 20 bytes
    keccak_hash = keccak(public_key_bytes[1:])  # skip 04 prefix
    addr_bytes = b'\x41' + keccak_hash[-20:]
    # base58check = base58(addr_bytes + sha256(sha256(addr_bytes))[:4])
    checksum = hashlib.sha256(hashlib.sha256(addr_bytes).digest()).digest()[:4]
    return base58.b58encode(addr_bytes + checksum).decode()


def derive_tron_address(seed: bytes, index: int) -> tuple[str, str]:
    """派生 TRON 地址

    Returns: (tron_address, private_key_hex)
    """
    bip32 = BIP32.from_seed(seed)
    private_key_bytes = bip32.get_privkey_from_path(f"m/44'/195'/0'/0/{index}")
    pk = eth_keys.PrivateKey(private_key_bytes)
    # eth_keys public_key 返回未压缩格式 (不含 04 前缀)
    uncompressed = b'\x04' + pk.public_key.to_bytes()
    address = _tron_address_from_public_key(uncompressed)
    return address, private_key_bytes.hex()


# ─── Public API ─────────────────────────────────────────

def generate_addresses(chain: str, start_index: int, count: int) -> list[tuple[int, str]]:
    """批量生成地址（不返回私钥）

    Returns: list of (index, address)
    """
    seed = _get_seed()
    derive_fn = derive_bsc_address if chain == "BSC" else derive_tron_address
    results = []
    for i in range(count):
        idx = start_index + i
        address, _ = derive_fn(seed, idx)
        results.append((idx, address))
    return results


def get_private_key(chain: str, index: int) -> str:
    """归集时调用 — 实时派生私钥"""
    seed = _get_seed()
    derive_fn = derive_bsc_address if chain == "BSC" else derive_tron_address
    _, private_key_hex = derive_fn(seed, index)
    return private_key_hex
