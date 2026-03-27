// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title TronMultiSig
 * @notice k-of-n 多签钱包，部署在 TVM 上。
 *         Owner 通过 TronLink signMessageV2 对消息 hash 签名（不签链上交易），
 *         后端收集够 k 个签名后调用 execute() 执行转账。
 *
 * 签名流程：
 *   1. 后端计算 msgHash = keccak256(address(this), token, to, amount, nonce)
 *   2. 后端将 msgHash 转为 hex 字符串（"0x" + 64 chars = 66 bytes）
 *   3. 前端调用 tronWeb.trx.signMessageV2(hexMsgHash) → 65字节签名
 *   4. 合约内 getTronSignedHash() 重现相同 hash，ecrecover 验证
 */

interface ITRC20 {
    function transfer(address to, uint256 amount) external;
    function balanceOf(address account) external view returns (uint256);
}

contract TronMultiSig {

    // ─── Events ───────────────────────────────────────────────────────────────
    event Executed(address indexed token, address indexed to, uint256 amount, uint256 nonce);
    event OwnerAdded(address indexed owner);
    event OwnerRemoved(address indexed owner);
    event ThresholdChanged(uint256 newThreshold);

    // ─── State ────────────────────────────────────────────────────────────────
    address[] public owners;
    mapping(address => bool) public isOwner;
    uint256 public threshold;
    uint256 public nonce;  // 防重放，每次执行后递增

    // ─── Constructor ──────────────────────────────────────────────────────────
    constructor(address[] memory _owners, uint256 _threshold) {
        require(_owners.length >= 1, "At least 1 owner required");
        require(_threshold >= 1 && _threshold <= _owners.length, "Invalid threshold");

        for (uint256 i = 0; i < _owners.length; i++) {
            address owner = _owners[i];
            require(owner != address(0), "Zero address not allowed");
            require(!isOwner[owner], "Duplicate owner");
            isOwner[owner] = true;
            owners.push(owner);
        }
        threshold = _threshold;
    }

    // ─── Main: Transfer USDT ──────────────────────────────────────────────────

    /**
     * @notice 执行 TRC-20 转账，需要 k 个 owner 签名
     * @param token   TRC-20 合约地址（USDT: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t）
     * @param to      收款地址
     * @param amount  金额（最小单位，USDT 为 6 位小数）
     * @param signatures  k 个签名，必须按签名人地址升序排列（防重复）
     */
    function execute(
        address token,
        address to,
        uint256 amount,
        bytes[] memory signatures
    ) external {
        require(to != address(0), "Invalid recipient");
        require(amount > 0, "Amount must be > 0");
        require(token != address(0), "Invalid token");

        bytes32 msgHash = getMessageHash(token, to, amount, nonce);
        _verifySignatures(msgHash, signatures);

        // 先改状态，再外部调用（防重入）
        nonce++;

        // 通过余额差值验证转账，兼容 TRON USDT 等不正确返回 bool 的 TRC20
        uint256 balBefore = ITRC20(token).balanceOf(address(this));
        (bool ok,) = address(token).call(
            abi.encodeWithSelector(ITRC20.transfer.selector, to, amount)
        );
        require(ok, "Token transfer call failed");
        uint256 balAfter = ITRC20(token).balanceOf(address(this));
        require(balBefore - balAfter >= amount, "Token transfer amount mismatch");

        emit Executed(token, to, amount, nonce - 1);
    }

    // ─── Owner Management ─────────────────────────────────────────────────────

    /**
     * @notice 添加新 owner，需要 k 个当前 owner 签名
     */
    function addOwner(address newOwner, bytes[] memory signatures) external {
        require(newOwner != address(0), "Zero address not allowed");
        require(!isOwner[newOwner], "Already an owner");

        bytes32 msgHash = keccak256(abi.encodePacked(
            address(this), bytes32("addOwner"), newOwner, nonce
        ));
        _verifySignatures(msgHash, signatures);

        nonce++;
        isOwner[newOwner] = true;
        owners.push(newOwner);

        emit OwnerAdded(newOwner);
    }

    /**
     * @notice 移除 owner，需要 k 个当前 owner 签名
     *         移除后不得使 owners 数量 < threshold
     */
    function removeOwner(address owner, bytes[] memory signatures) external {
        require(isOwner[owner], "Not an owner");
        require(owners.length - 1 >= threshold, "Would fall below threshold");

        bytes32 msgHash = keccak256(abi.encodePacked(
            address(this), bytes32("removeOwner"), owner, nonce
        ));
        _verifySignatures(msgHash, signatures);

        nonce++;
        isOwner[owner] = false;

        // 用末尾元素填充，避免数组空洞
        for (uint256 i = 0; i < owners.length; i++) {
            if (owners[i] == owner) {
                owners[i] = owners[owners.length - 1];
                owners.pop();
                break;
            }
        }

        emit OwnerRemoved(owner);
    }

    /**
     * @notice 修改签名门槛，需要 k 个当前 owner 签名
     */
    function changeThreshold(uint256 newThreshold, bytes[] memory signatures) external {
        require(newThreshold >= 1 && newThreshold <= owners.length, "Invalid threshold");

        bytes32 msgHash = keccak256(abi.encodePacked(
            address(this), bytes32("changeThreshold"), newThreshold, nonce
        ));
        _verifySignatures(msgHash, signatures);

        nonce++;
        threshold = newThreshold;

        emit ThresholdChanged(newThreshold);
    }

    // ─── View Helpers ─────────────────────────────────────────────────────────

    function getOwners() external view returns (address[] memory) {
        return owners;
    }

    function getOwnersCount() external view returns (uint256) {
        return owners.length;
    }

    /**
     * @notice 计算 execute() 的消息 hash（供后端/前端计算签名内容）
     *         包含 address(this) 防止跨合约重放
     */
    function getMessageHash(
        address token,
        address to,
        uint256 amount,
        uint256 _nonce
    ) public view returns (bytes32) {
        return keccak256(abi.encodePacked(
            address(this), token, to, amount, _nonce
        ));
    }

    /**
     * @notice 还原 TronLink signMessageV2 的实际签名 hash
     *
     * TronLink signMessageV2(hexStr) 签名流程：
     *   msgBytes = hexStr.encode("utf-8")   // "0x" + 64 hex = 66 bytes
     *   prefix   = "\x19TRON Signed Message:\n66"
     *   return   = keccak256(prefix + msgBytes)
     *
     * 合约中：先把 bytes32 转为 hex 字符串（含 "0x"），再套 TRON 前缀
     */
    function getTronSignedHash(bytes32 msgHash) public pure returns (bytes32) {
        bytes memory hexStr = _toHexString(msgHash); // "0x" + 64 chars = 66 bytes
        bytes memory prefix = "\x19TRON Signed Message:\n66";
        return keccak256(abi.encodePacked(prefix, hexStr));
    }

    // ─── Internals ────────────────────────────────────────────────────────────

    /**
     * @dev 验证签名集合：
     *      - 每个签名必须来自 owner
     *      - 签名人地址必须严格升序（防重复签名、防乱序重放）
     *      - 有效签名数 >= threshold
     */
    function _verifySignatures(bytes32 msgHash, bytes[] memory signatures) internal view {
        bytes32 signedHash = getTronSignedHash(msgHash);
        uint256 validCount = 0;
        address lastSigner = address(0);

        for (uint256 i = 0; i < signatures.length; i++) {
            address signer = _recoverSigner(signedHash, signatures[i]);
            // 严格升序：防止同一签名人提交两次，也防止乱序攻击
            require(signer > lastSigner, "Signatures must be sorted ascending and unique");
            require(isOwner[signer], "Signer is not an owner");
            lastSigner = signer;
            validCount++;
        }

        require(validCount >= threshold, "Not enough valid signatures");
    }

    /**
     * @dev ECDSA 恢复签名人地址
     *      - 拒绝长度不为 65 的签名
     *      - 规范化 v（TronLink 可能返回 0/1，需加 27）
     *      - 拒绝 s 值在上半曲线（防签名可塑性攻击）
     */
    function _recoverSigner(bytes32 hash, bytes memory sig) internal pure returns (address) {
        require(sig.length == 65, "Invalid signature length");

        bytes32 r;
        bytes32 s;
        uint8 v;

        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }

        // TronLink 有时返回 v=0/1，需归一化为 27/28
        if (v < 27) v += 27;
        require(v == 27 || v == 28, "Invalid v value");

        // 防签名可塑性：s 必须在下半曲线
        require(
            uint256(s) <= 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0,
            "Malleable signature (s too high)"
        );

        address signer = ecrecover(hash, v, r, s);
        require(signer != address(0), "ecrecover returned zero address");

        return signer;
    }

    /**
     * @dev 将 bytes32 转为小写 hex 字符串（含 "0x" 前缀），共 66 字节
     *      与 Python hex()/Web3.to_hex() 输出一致
     */
    function _toHexString(bytes32 data) internal pure returns (bytes memory) {
        bytes memory hexAlpha = "0123456789abcdef";
        bytes memory result = new bytes(66); // "0x" + 64 hex chars
        result[0] = "0";
        result[1] = "x";
        for (uint256 i = 0; i < 32; i++) {
            uint8 b = uint8(data[i]);
            result[2 + i * 2]     = hexAlpha[b >> 4];
            result[2 + i * 2 + 1] = hexAlpha[b & 0x0f];
        }
        return result;
    }

    // 允许合约接收 TRX（用于补充能量/带宽费用）
    receive() external payable {}
}
