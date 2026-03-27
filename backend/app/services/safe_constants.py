"""
Gnosis Safe v1.3.0 合约常量 — BSC 主网 (chain_id=56)

仅包含本系统用到的 ABI 片段，不引入完整 Safe 依赖。
"""

# ─── 合约地址 ──────────────────────────────────────────

SAFE_PROXY_FACTORY = "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2"
SAFE_SINGLETON = "0xd9Db270c1B5E3Bd161E8c8503c55cEABeE709552"
SAFE_FALLBACK_HANDLER = "0xf48f2B2d2a534e402487b3ee7C18c33Aec0Fe5e4"

# ─── Safe ABI（最小化）──────────────────────────────────

SAFE_ABI = [
    # setup(address[],uint256,address,bytes,address,address,uint256,address)
    {
        "inputs": [
            {"name": "_owners", "type": "address[]"},
            {"name": "_threshold", "type": "uint256"},
            {"name": "to", "type": "address"},
            {"name": "data", "type": "bytes"},
            {"name": "fallbackHandler", "type": "address"},
            {"name": "paymentToken", "type": "address"},
            {"name": "payment", "type": "uint256"},
            {"name": "paymentReceiver", "type": "address"},
        ],
        "name": "setup",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    # getOwners() → address[]
    {
        "inputs": [],
        "name": "getOwners",
        "outputs": [{"name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    # getThreshold() → uint256
    {
        "inputs": [],
        "name": "getThreshold",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    # nonce() → uint256
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    # execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes) → bool
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"},
        ],
        "name": "execTransaction",
        "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

# ─── ProxyFactory ABI（最小化）─────────────────────────

PROXY_FACTORY_ABI = [
    # createProxyWithNonce(address,bytes,uint256) → address
    {
        "inputs": [
            {"name": "_singleton", "type": "address"},
            {"name": "initializer", "type": "bytes"},
            {"name": "saltNonce", "type": "uint256"},
        ],
        "name": "createProxyWithNonce",
        "outputs": [{"name": "proxy", "type": "address"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    # event ProxyCreation(address proxy, address singleton)
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "name": "proxy", "type": "address"},
            {"indexed": False, "name": "singleton", "type": "address"},
        ],
        "name": "ProxyCreation",
        "type": "event",
    },
]
