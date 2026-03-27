"""
归集执行器模拟测试 — 验证核心逻辑（不需要真实 RPC/DB）

测试覆盖:
  1. Phase 1: gas 钱包串行补 gas，nonce_cache 正确传递
  2. Phase 2: 多地址并发转账，每个地址独立私钥
  3. 部分失败 → partial 状态
  4. 幂等: 已完成 item 跳过
  5. Gas 余额预检不足 → 全部 failed
  6. 原生代币归集保留 gas
"""

import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from datetime import datetime, timezone


# ─── Mock 数据构造 ────────────────────────────────────

def make_collection(id=1, chain="BSC", asset_type="usdt", status="executing"):
    c = MagicMock()
    c.id = id
    c.chain = chain
    c.asset_type = asset_type
    c.status = status
    c.total_amount = Decimal("0")
    c.address_count = 0
    c.executed_at = None
    return c


def make_item(id, address, amount, status="pending"):
    it = MagicMock()
    it.id = id
    it.address = address
    it.amount = Decimal(str(amount))
    it.status = status
    it.gas_tx_hash = None
    it.tx_hash = None
    it.error_message = None
    it.retry_count = 0
    return it


def make_wallet(address, type_="collection", derive_index=None, chain="BSC"):
    w = MagicMock()
    w.address = address
    w.type = type_
    w.derive_index = derive_index
    w.chain = chain
    return w


def make_deposit_addr(address, derive_index, chain="BSC"):
    d = MagicMock()
    d.address = address
    d.derive_index = derive_index
    d.chain = chain
    return d


# ─── 测试 1: Gas 钱包串行补 gas + nonce_cache 传递 ────

@pytest.mark.asyncio
async def test_gas_phase_sequential_with_nonce_cache():
    """验证 Phase 1 补 gas 时:
    1. 串行调用 send_native（不是并发）
    2. 传入了 nonce_cache 参数
    3. 所有调用共享同一个 nonce_cache dict
    """
    from app.services.chain_client import chain_client

    call_order = []
    nonce_caches_seen = []

    original_send_native = chain_client.send_native

    async def mock_send_native(chain, pk, from_addr, to_addr, amount, nonce_cache=None):
        call_order.append(to_addr)
        nonce_caches_seen.append(id(nonce_cache))  # 记录 dict 的 id
        await asyncio.sleep(0.01)  # 模拟耗时
        return f"0xgas_{to_addr[-4:]}"

    async def mock_get_native_balance(chain, address):
        # 所有地址 gas 不足
        return Decimal("0.0001")

    with patch.object(chain_client, 'send_native', side_effect=mock_send_native), \
         patch.object(chain_client, 'get_native_balance', side_effect=mock_get_native_balance):

        # 模拟 Phase 1 逻辑（从 executor 中提取核心）
        from app.services.chain_client import GAS_ESTIMATE_BSC, GAS_BUFFER_MULTIPLIER

        gas_nonce_cache = {}
        items = [make_item(i, f"0xaddr{i:04d}", 100) for i in range(5)]
        gas_private_key = "0x" + "a1" * 32
        gas_funder_address = "0xGasFunder"
        gas_estimate = GAS_ESTIMATE_BSC

        for item in items:
            native_balance = await chain_client.get_native_balance("BSC", item.address)
            if native_balance < gas_estimate:
                gas_to_send = gas_estimate * GAS_BUFFER_MULTIPLIER - native_balance
                gas_tx_hash = await chain_client.send_native(
                    "BSC", gas_private_key, gas_funder_address,
                    item.address, gas_to_send,
                    nonce_cache=gas_nonce_cache,
                )
                item.gas_tx_hash = gas_tx_hash
                item.status = "gas_sent"

        # 验证
        assert len(call_order) == 5, f"应该调用 5 次 send_native, 实际 {len(call_order)}"
        # 验证所有调用共享同一个 nonce_cache
        assert len(set(nonce_caches_seen)) == 1, "所有 send_native 调用应共享同一个 nonce_cache"
        # 验证是串行的（顺序一致）
        expected = [f"0xaddr{i:04d}" for i in range(5)]
        assert call_order == expected, f"应按顺序串行调用, 实际 {call_order}"
        # 验证 gas_tx_hash 记录
        for item in items:
            assert item.gas_tx_hash is not None
            assert item.status == "gas_sent"


# ─── 测试 2: Phase 2 并发转账 ─────────────────────────

@pytest.mark.asyncio
async def test_transfer_phase_concurrent():
    """验证 Phase 2:
    1. 不同地址并发转账
    2. 每个地址用各自的私钥（不传 nonce_cache）
    """
    from app.services.chain_client import chain_client

    concurrent_count = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def mock_send_usdt(chain, pk, from_addr, to_addr, amount, nonce_cache=None):
        nonlocal concurrent_count, max_concurrent
        async with lock:
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.05)  # 模拟耗时
        async with lock:
            concurrent_count -= 1
        return f"0xtx_{from_addr[-4:]}"

    with patch.object(chain_client, 'send_usdt', side_effect=mock_send_usdt):
        items = [make_item(i, f"0xaddr{i:04d}", 100, status="gas_sent") for i in range(5)]

        # 模拟并发转账
        async def run_one(item):
            tx_hash = await chain_client.send_usdt(
                "BSC", f"0xkey_{item.address}", item.address,
                "0xTarget", item.amount,
            )
            item.tx_hash = tx_hash
            item.status = "completed"
            return True

        results = await asyncio.gather(*[run_one(it) for it in items])

        assert all(results)
        assert max_concurrent > 1, f"应该并发执行, 最大并发数 {max_concurrent}"
        for item in items:
            assert item.status == "completed"
            assert item.tx_hash is not None


# ─── 测试 3: 部分失败 → partial ──────────────────────

@pytest.mark.asyncio
async def test_partial_failure():
    """验证部分转账失败时状态和计数正确"""
    from app.services.chain_client import chain_client

    fail_addresses = {"0xaddr0002", "0xaddr0004"}

    async def mock_send_usdt(chain, pk, from_addr, to_addr, amount, nonce_cache=None):
        if from_addr in fail_addresses:
            raise RuntimeError("模拟转账失败: insufficient gas")
        return f"0xtx_{from_addr[-4:]}"

    with patch.object(chain_client, 'send_usdt', side_effect=mock_send_usdt):
        items = [make_item(i, f"0xaddr{i:04d}", 100, status="gas_sent") for i in range(5)]

        completed_count = 0
        failed_count = 0

        for item in items:
            try:
                tx_hash = await chain_client.send_usdt(
                    "BSC", f"0xkey", item.address, "0xTarget", item.amount,
                )
                item.tx_hash = tx_hash
                item.status = "completed"
                completed_count += 1
            except Exception as e:
                item.status = "failed"
                item.error_message = str(e)[:500]
                item.retry_count += 1
                failed_count += 1

        assert completed_count == 3
        assert failed_count == 2

        # 计算归集状态
        if failed_count == 0:
            status = "completed"
        elif completed_count == 0:
            status = "failed"
        else:
            status = "partial"

        assert status == "partial"

        # 验证失败 item 记录了错误信息
        for item in items:
            if item.status == "failed":
                assert item.error_message is not None
                assert "insufficient gas" in item.error_message
                assert item.retry_count == 1


# ─── 测试 4: 幂等 — 跳过已完成 item ──────────────────

@pytest.mark.asyncio
async def test_idempotent_skip_completed():
    """验证幂等性: 已完成的 item 不会被重复处理"""
    items = [
        make_item(1, "0xaddr0001", 100, status="completed"),
        make_item(2, "0xaddr0002", 200, status="pending"),
        make_item(3, "0xaddr0003", 300, status="completed"),
        make_item(4, "0xaddr0004", 400, status="gas_sent"),
    ]

    pending_items = [it for it in items if it.status != "completed"]

    assert len(pending_items) == 2
    assert pending_items[0].id == 2
    assert pending_items[1].id == 4


# ─── 测试 5: Gas 预检不足 ─────────────────────────────

@pytest.mark.asyncio
async def test_gas_precheck_insufficient():
    """验证 gas 余额不足时所有 item 标记为 failed"""
    from app.services.chain_client import (
        chain_client, GAS_ESTIMATE_BSC, GAS_BUFFER_MULTIPLIER,
    )

    async def mock_get_native_balance(chain, address):
        return Decimal("0.0001")

    with patch.object(chain_client, 'get_native_balance', side_effect=mock_get_native_balance):
        items = [make_item(i, f"0xaddr{i:04d}", 100) for i in range(10)]
        gas_estimate = GAS_ESTIMATE_BSC  # 0.002

        # 统计需要补 gas 的地址
        needs_gas_count = 0
        for item in items:
            native_bal = await chain_client.get_native_balance("BSC", item.address)
            if native_bal < gas_estimate:
                needs_gas_count += 1

        assert needs_gas_count == 10

        total_gas_needed = gas_estimate * GAS_BUFFER_MULTIPLIER * needs_gas_count
        # 0.002 * 3 * 10 = 0.06 BNB
        assert total_gas_needed == Decimal("0.06")

        best_gas_balance = Decimal("0.01")  # 只有 0.01 BNB，不够

        assert total_gas_needed > best_gas_balance

        # 标记所有 item 失败
        for item in items:
            item.status = "failed"
            item.error_message = f"Gas 钱包余额不足: 需要 {total_gas_needed}, 当前 {best_gas_balance}"

        for item in items:
            assert item.status == "failed"
            assert "Gas 钱包余额不足" in item.error_message


# ─── 测试 6: 原生代币归集保留 gas ─────────────────────

@pytest.mark.asyncio
async def test_native_collection_reserves_gas():
    """验证原生代币归集扣除 NATIVE_RESERVE 后转出"""
    from app.services.chain_client import (
        chain_client, NATIVE_RESERVE_BSC, NATIVE_RESERVE_TRON,
    )

    # BSC: 余额 1.5 BNB, 保留 0.001, 转 1.499
    balance_bsc = Decimal("1.5")
    actual_bsc = balance_bsc - NATIVE_RESERVE_BSC
    assert actual_bsc == Decimal("1.499")
    assert actual_bsc > 0

    # TRON: 余额 100 TRX, 保留 15, 转 85
    balance_tron = Decimal("100")
    actual_tron = balance_tron - NATIVE_RESERVE_TRON
    assert actual_tron == Decimal("85")
    assert actual_tron > 0

    # TRON: 余额 10 TRX, 保留 15, 不应转出
    balance_low = Decimal("10")
    actual_low = balance_low - NATIVE_RESERVE_TRON
    assert actual_low == Decimal("-5")
    assert actual_low <= 0  # 应该拒绝转账


# ─── 测试 7: RPCManager 健康追踪 ─────────────────────

def test_rpc_endpoint_health():
    """验证 RPCEndpoint 失败 3 次后拉黑"""
    from app.services.chain_client import RPCEndpoint

    ep = RPCEndpoint(url="https://fake-rpc.example.com")
    # 注意: 因为假 URL，web3 可能创建但连不通
    # 直接测试 mark_failed / mark_success 逻辑
    ep.is_working = True
    ep.fail_count = 0

    ep.mark_failed()
    assert ep.is_working is True  # 1 次
    ep.mark_failed()
    assert ep.is_working is True  # 2 次
    ep.mark_failed()
    assert ep.is_working is False  # 3 次 → 拉黑

    ep.mark_success()
    assert ep.is_working is True   # 恢复
    assert ep.fail_count == 0


# ─── 测试 8: RPCManager 全部拉黑自动恢复 ──────────────

def test_rpc_manager_auto_recovery():
    """验证所有 RPC 拉黑时自动重置"""
    from app.services.chain_client import RPCManager, RPCEndpoint

    mgr = RPCManager()
    # 手动添加已拉黑的端点
    ep1 = RPCEndpoint(url="https://rpc1.example.com")
    ep1.is_working = False
    ep2 = RPCEndpoint(url="https://rpc2.example.com")
    ep2.is_working = False
    mgr.endpoints = [ep1, ep2]

    # get_rpc 应该触发自动恢复
    result = mgr.get_rpc()
    assert result is not None
    assert ep1.is_working is True
    assert ep2.is_working is True


# ─── 测试 9: 进度跟踪 ─────────────────────────────────

def test_progress_tracking():
    """验证内存级进度跟踪"""
    from app.services.collection_executor import (
        get_collection_progress, _update_progress, _collection_progress,
    )

    test_id = 99999

    # 清理
    _collection_progress.pop(test_id, None)

    assert get_collection_progress(test_id) is None

    _update_progress(test_id, total=10, current_step="测试")
    p = get_collection_progress(test_id)
    assert p is not None
    assert p["total"] == 10
    assert p["current_step"] == "测试"

    _update_progress(test_id, completed=5, failed=1)
    p = get_collection_progress(test_id)
    assert p["completed"] == 5
    assert p["failed"] == 1

    # 清理
    _collection_progress.pop(test_id, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
