"""
归集执行服务 — 后台异步执行归集批次

由 API 端点通过 asyncio.create_task() 启动。

执行流程:
  1. Gas 余额预检 — 估算总需 gas，检查 gas 钱包余额是否足够
  2. Phase 1: 补 gas（串行） — gas 钱包逐个向需要补 gas 的地址发送 BNB/TRX
     同一个 gas 钱包串行发送，避免 nonce 冲突
  3. Phase 2: 转 USDT/原生代币（并发） — 每个充值地址独立私钥，10 个一批并发
  4. 更新状态 + 发送通知

幂等保护: 跳过已 completed 的 item，崩溃后可安全重跑
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.collection import Collection, CollectionItem
from app.models.deposit_address import DepositAddress
from app.models.wallet import Wallet
from app.core.hdwallet import get_private_key
from app.core.telegram import notifier
from app.services.chain_client import (
    chain_client,
    GAS_ESTIMATE_BSC, GAS_ESTIMATE_TRON, GAS_BUFFER_MULTIPLIER,
    NATIVE_RESERVE_BSC, NATIVE_RESERVE_TRON,
)
from app.services.tron_energy import tron_energy_service, estimate_transfer_energy

logger = logging.getLogger(__name__)

# ─── 进度跟踪（内存级，供 API 查询） ────────────────────

_collection_progress: dict[int, dict] = {}


def get_collection_progress(collection_id: int) -> dict | None:
    """获取归集执行进度（供 API 调用）"""
    return _collection_progress.get(collection_id)


def _update_progress(collection_id: int, **kwargs):
    """更新进度"""
    if collection_id not in _collection_progress:
        _collection_progress[collection_id] = {
            "total": 0, "completed": 0, "failed": 0,
            "gas_phase": False, "transfer_phase": False,
            "current_step": "", "started_at": None,
        }
    _collection_progress[collection_id].update(kwargs)


# ─── 入口 ──────────────────────────────────────────────

async def execute_collection(collection_id: int):
    """归集批次执行入口（后台任务）"""
    try:
        _update_progress(collection_id, started_at=datetime.now(timezone.utc).isoformat())
        async with AsyncSessionLocal() as db:
            await _do_execute(db, collection_id)
    except Exception as e:
        logger.error("归集 %d 执行异常: %s", collection_id, e, exc_info=True)
        _update_progress(collection_id, current_step=f"异常终止: {str(e)[:100]}")
        try:
            async with AsyncSessionLocal() as db:
                collection = (await db.execute(
                    select(Collection).where(Collection.id == collection_id)
                )).scalar_one_or_none()
                if collection and collection.status == "executing":
                    collection.status = "failed"
                    await db.commit()
        except Exception:
            pass
    finally:
        pass


async def _do_execute(db: AsyncSession, collection_id: int):
    # ── 1. 加载归集批次和明细 ──────────────────────────
    collection = (await db.execute(
        select(Collection).where(Collection.id == collection_id)
    )).scalar_one_or_none()
    if not collection:
        logger.error("归集 %d 不存在", collection_id)
        return

    items = (await db.execute(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection_id
        ).order_by(CollectionItem.id)
    )).scalars().all()

    # 幂等: 过滤掉已完成的
    pending_items = [it for it in items if it.status != "completed"]
    if not pending_items:
        logger.info("归集 #%d 所有 item 已完成，跳过", collection_id)
        collection.status = "completed"
        await db.commit()
        return

    chain = collection.chain
    asset_type = getattr(collection, "asset_type", "usdt") or "usdt"
    logger.info("开始执行归集 #%d (%s, %s, %d 个地址, %d 待处理)",
                collection_id, chain, asset_type, len(items), len(pending_items))

    _update_progress(collection_id, total=len(pending_items), current_step="加载配置")

    # ── 2. 加载归集目标钱包 ────────────────────────────
    wallet = (await db.execute(
        select(Wallet).where(Wallet.chain == chain, Wallet.type == "collection")
        .order_by(Wallet.id).limit(1)
    )).scalar_one_or_none()
    if not wallet or not wallet.address:
        collection.status = "failed"
        await db.commit()
        logger.error("%s 归集钱包未配置", chain)
        return

    target_address = wallet.address

    # ── 3. 加载 Gas 钱包 ──────────────────────────────
    gas_wallets = (await db.execute(
        select(Wallet).where(Wallet.chain == chain, Wallet.type == "gas")
    )).scalars().all()

    if not gas_wallets:
        collection.status = "failed"
        await db.commit()
        logger.error("%s 未配置 Gas 钱包", chain)
        return

    best_gas_wallet = None
    best_gas_balance = Decimal(-1)
    for gw in gas_wallets:
        if not gw.address or gw.derive_index is None:
            continue
        try:
            bal = await chain_client.get_native_balance(chain, gw.address)
            if bal > best_gas_balance:
                best_gas_balance = bal
                best_gas_wallet = gw
        except Exception as e:
            logger.warning("查询 Gas 钱包 %s 余额失败: %s", gw.address[:10], e)

    if not best_gas_wallet:
        collection.status = "failed"
        await db.commit()
        logger.error("%s 无可用 Gas 钱包", chain)
        return

    gas_private_key = get_private_key(chain, best_gas_wallet.derive_index)
    gas_funder_address = best_gas_wallet.address
    gas_estimate = GAS_ESTIMATE_BSC if chain == "BSC" else GAS_ESTIMATE_TRON

    logger.info("使用 Gas 钱包 %s (derive_index=%d, 余额=%s)",
                gas_funder_address[:10], best_gas_wallet.derive_index, best_gas_balance)

    # ── 4. Gas 余额预检（仅 USDT 归集需要补 gas）──────
    if asset_type == "usdt":
        _update_progress(collection_id, current_step="Gas 余额预检")

        # 统计需要补 gas 的地址数量
        needs_gas_count = 0
        for item in pending_items:
            try:
                native_bal = await chain_client.get_native_balance(chain, item.address)
                if native_bal < gas_estimate:
                    needs_gas_count += 1
            except Exception:
                needs_gas_count += 1  # 查不到余额的也算需要补

        total_gas_needed = gas_estimate * GAS_BUFFER_MULTIPLIER * needs_gas_count
        if total_gas_needed > best_gas_balance:
            logger.error(
                "Gas 余额不足! 需要 %s %s (补 %d 个地址), 当前余额 %s",
                total_gas_needed, "BNB" if chain == "BSC" else "TRX",
                needs_gas_count, best_gas_balance,
            )
            collection.status = "failed"
            for item in pending_items:
                item.status = "failed"
                item.error_message = f"Gas 钱包余额不足: 需要 {total_gas_needed}, 当前 {best_gas_balance}"
            await db.commit()
            return

        logger.info("Gas 预检通过: 需补 %d 个地址, 预计消耗 %s %s",
                     needs_gas_count, total_gas_needed, "BNB" if chain == "BSC" else "TRX")

    # ── 5. 预加载 derive_index ────────────────────────
    addr_rows = (await db.execute(
        select(DepositAddress).where(
            DepositAddress.chain == chain,
            DepositAddress.address.in_([it.address for it in pending_items]),
        )
    )).scalars().all()
    derive_map = {a.address: a.derive_index for a in addr_rows}

    # ── 6. Phase 1: 补 gas（串行）─────────────────────
    # gas 钱包同一个私钥，必须串行发送保证 nonce 顺序
    # 使用本地 nonce_cache（和 worker 一样），批次结束自动丢弃
    if asset_type == "usdt":
        _update_progress(collection_id, gas_phase=True, current_step="补充 Gas")
        gas_sent_count = 0
        gas_nonce_cache: dict = {}  # 本地 nonce 缓存，串行补 gas 期间复用

        for i, item in enumerate(pending_items):
            if item.status == "gas_sent":
                # 已补过 gas（崩溃恢复场景），跳过
                gas_sent_count += 1
                continue

            try:
                native_balance = await chain_client.get_native_balance(chain, item.address)
                if native_balance < gas_estimate:
                    gas_to_send = gas_estimate * GAS_BUFFER_MULTIPLIER - native_balance
                    logger.info("[Gas %d/%d] 补 gas: %s %s → %s",
                                i + 1, len(pending_items), gas_to_send,
                                "BNB" if chain == "BSC" else "TRX", item.address[:10])

                    gas_tx_hash = await chain_client.send_native(
                        chain, gas_private_key, gas_funder_address,
                        item.address, gas_to_send,
                        nonce_cache=gas_nonce_cache,
                    )
                    item.gas_tx_hash = gas_tx_hash
                    item.status = "gas_sent"
                    gas_sent_count += 1
                    await db.commit()
                else:
                    # gas 够，直接标记
                    item.status = "gas_sent"
                    await db.commit()
                    logger.debug("[Gas %d/%d] %s gas 充足 (%s), 跳过",
                                 i + 1, len(pending_items), item.address[:10], native_balance)
            except Exception as e:
                item.status = "failed"
                item.error_message = f"补 Gas 失败: {str(e)[:400]}"
                item.retry_count += 1
                await db.commit()
                logger.error("[Gas %d/%d] 补 gas 失败 (%s): %s",
                             i + 1, len(pending_items), item.address[:10], e)

            _update_progress(collection_id,
                             current_step=f"补 Gas: {i + 1}/{len(pending_items)}")

        logger.info("Gas 补充完成: %d/%d 成功", gas_sent_count, len(pending_items))

        # 等待 gas 到账（BSC ~3s, TRON ~3s）
        if gas_sent_count > 0:
            await asyncio.sleep(5)

    # ── 6.5. TRON 能量预租赁（串行，避免 feee.io 限频）───
    if chain == "TRON" and asset_type == "usdt":
        _update_progress(collection_id, current_step="租赁能量")
        try:
            s = await chain_client._load_settings()
            if s.tron_energy_rental_enabled and s.tron_energy_rental_api_url:
                api_urls = s.tron_api_urls or []
                api_keys_list = s.tron_api_keys or []
                rental_api_url = (s.tron_energy_rental_api_url or "").strip()
                rental_api_key = (s.tron_energy_rental_api_key or "").strip()
                rental_max_price = s.tron_energy_rental_max_price or 150
                rental_duration = s.tron_energy_rental_duration or 3_600_000

                energy_items = [it for it in pending_items if it.status in ("gas_sent", "pending")]
                for i, item in enumerate(energy_items):
                    try:
                        estimated = await estimate_transfer_energy(
                            api_urls, api_keys_list,
                            item.address, target_address,
                            int(item.amount * 1_000_000),
                            s.tron_usdt_contract or "",
                        )
                        result = await tron_energy_service.ensure_energy(
                            api_urls, api_keys_list, item.address,
                            energy_needed=estimated,
                            rental_enabled=True,
                            rental_api_url=rental_api_url,
                            rental_api_key=rental_api_key,
                            rental_max_price_sun=rental_max_price,
                            rental_duration_ms=rental_duration,
                        )
                        if result.get("sufficient"):
                            if result.get("rented"):
                                logger.info("[能量 %d/%d] %s 租赁成功并已到账", i + 1, len(energy_items), item.address[:10])
                            else:
                                logger.info("[能量 %d/%d] %s 能量充足，无需租赁", i + 1, len(energy_items), item.address[:10])
                        else:
                            logger.warning("[能量 %d/%d] %s 能量未就绪（将烧 TRX）: %s", i + 1, len(energy_items), item.address[:10], result.get("error"))
                    except Exception as e:
                        logger.warning("[能量 %d/%d] %s 异常: %s", i + 1, len(energy_items), item.address[:10], e)

                    # feee.io 限频保护：每次请求间隔 2 秒
                    if i < len(energy_items) - 1:
                        await asyncio.sleep(2)

                _update_progress(collection_id, current_step="能量租赁完成")
                logger.info("TRON 能量预租赁完成: %d 个地址", len(energy_items))
        except Exception as e:
            logger.warning("TRON 能量预租赁异常(不影响转账): %s", e)

    # ── 7. Phase 2: 转账（并发，10 个一批）─────────────
    _update_progress(collection_id, gas_phase=False, transfer_phase=True,
                     current_step="转账中")

    # 只处理状态为 gas_sent 或 pending(原生代币) 的 item
    transfer_items = [
        it for it in pending_items
        if it.status in ("gas_sent", "pending")
    ]

    BATCH_SIZE = 10
    completed_count = 0
    failed_count = 0
    completed_amount = Decimal(0)
    native_reserve = NATIVE_RESERVE_BSC if chain == "BSC" else NATIVE_RESERVE_TRON

    for batch_start in range(0, len(transfer_items), BATCH_SIZE):
        batch = transfer_items[batch_start:batch_start + BATCH_SIZE]

        async def _run_one(item: CollectionItem):
            """独立 session 处理单个 item 转账"""
            async with AsyncSessionLocal() as item_db:
                try:
                    await _process_transfer(
                        item_db, item, chain, target_address,
                        derive_map, asset_type, native_reserve,
                    )
                    await item_db.commit()
                    return True
                except Exception as e:
                    item.status = "failed"
                    item.error_message = str(e)[:500]
                    item.retry_count += 1
                    await item_db.commit()
                    logger.error("归集转账 item #%d 失败 (%s): %s",
                                 item.id, item.address[:10], e)
                    return False

        results = await asyncio.gather(*[_run_one(it) for it in batch])

        for item, ok in zip(batch, results):
            if ok:
                completed_count += 1
                completed_amount += item.amount
                logger.info("归集 item #%d 完成: %s → %s (tx: %s)",
                            item.id, item.address[:10], item.amount,
                            item.tx_hash[:16] if item.tx_hash else "")
            else:
                failed_count += 1

        _update_progress(
            collection_id,
            completed=completed_count,
            failed=failed_count,
            current_step=f"转账: {batch_start + len(batch)}/{len(transfer_items)}",
        )

        await db.commit()
        if batch_start + BATCH_SIZE < len(transfer_items):
            await asyncio.sleep(1)

    # 加上 gas 阶段已失败的数量
    gas_failed = sum(1 for it in pending_items if it.status == "failed" and it not in transfer_items)
    failed_count += gas_failed

    # ── 8. 更新归集状态 ───────────────────────────────
    collection.executed_at = datetime.now(timezone.utc)
    collection.total_amount = completed_amount
    collection.address_count = completed_count

    if failed_count == 0:
        collection.status = "completed"
    elif completed_count == 0:
        collection.status = "failed"
    else:
        collection.status = "partial"  # 部分成功

    await db.commit()

    _update_progress(
        collection_id,
        completed=completed_count,
        failed=failed_count,
        transfer_phase=False,
        current_step="完成" if failed_count == 0 else f"完成(失败 {failed_count} 个)",
    )

    # ── 9. 通知 ──────────────────────────────────────
    try:
        status_text = "全部成功" if failed_count == 0 else f"部分完成(失败 {failed_count})"
        await notifier.notify_collection_completed(
            chain=chain,
            total_amount=completed_amount,
            address_count=completed_count,
            extra_text=status_text,
        )
    except Exception as e:
        logger.warning("归集通知发送失败: %s", e)

    logger.info("归集 #%d 完成: 成功 %d, 失败 %d, 共 %s",
                collection_id, completed_count, failed_count, completed_amount)


# ─── 单个 item 转账 ──────────────────────────────────────

async def _process_transfer(
    db: AsyncSession,
    item: CollectionItem,
    chain: str,
    target_address: str,
    derive_map: dict[str, int],
    asset_type: str,
    native_reserve: Decimal,
):
    """处理单个归集明细的转账步骤（补 gas 已在 Phase 1 完成）"""

    # 查找 derive_index
    if item.address in derive_map:
        derive_index = derive_map[item.address]
    else:
        addr_row = (await db.execute(
            select(DepositAddress).where(
                DepositAddress.chain == chain,
                DepositAddress.address == item.address,
            )
        )).scalar_one_or_none()
        if not addr_row:
            raise RuntimeError(f"充值地址 {item.address} 不存在")
        derive_index = addr_row.derive_index

    deposit_private_key = get_private_key(chain, derive_index)

    item.status = "transferring"
    await db.flush()

    if asset_type == "native":
        # ─── 归集原生代币（BNB/TRX）───
        # 实时读取余额，扣除预留 gas 后转出
        current_balance = await chain_client.get_native_balance(chain, item.address)
        actual_amount = current_balance - native_reserve

        if actual_amount <= 0:
            raise RuntimeError(
                f"原生余额不足: 当前 {current_balance}, 预留 {native_reserve}, 可转 {actual_amount}"
            )

        tx_hash = await chain_client.send_native(
            chain, deposit_private_key, item.address,
            target_address, actual_amount,
        )
        item.tx_hash = tx_hash
        item.amount = actual_amount  # 更新为实际转账金额
        item.status = "completed"
    else:
        # ─── 归集 USDT ───
        # TRON 能量已在 Phase 2 前预租赁，跳过重复租赁
        tx_hash = await chain_client.send_usdt(
            chain, deposit_private_key, item.address,
            target_address, item.amount,
            skip_energy_rental=(chain == "TRON"),
        )
        item.tx_hash = tx_hash
        item.status = "completed"
