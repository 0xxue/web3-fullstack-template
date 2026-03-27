"""
打款执行服务 — 后台异步执行打款批次

由签名提案达到阈值后通过 asyncio.create_task() 启动。

执行流程:
  1. 加载批次 + 明细 + 打款钱包私钥
  2. TRON USDT: 逐笔能量估算 + 租赁（串行，feee.io 限频 2s 间隔）
  3. 串行转账（同一私钥必须串行保证 nonce/sequence 顺序）
  4. 更新状态 + 发送通知

幂等保护: 跳过已 completed 的 item，崩溃后可安全重跑
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.payout import Payout, PayoutItem
from app.models.wallet import Wallet
from app.core.hdwallet import get_private_key
from app.core.telegram import notifier
from app.services.chain_client import chain_client, GAS_ESTIMATE_TRON, GAS_BUFFER_MULTIPLIER
from app.services.tron_energy import tron_energy_service, estimate_transfer_energy

logger = logging.getLogger(__name__)

# ─── 进度跟踪（内存级，供 API 查询）────────────────────

_payout_progress: dict[int, dict] = {}


def get_payout_progress(payout_id: int) -> dict | None:
    return _payout_progress.get(payout_id)


def _update_progress(payout_id: int, **kwargs):
    if payout_id not in _payout_progress:
        _payout_progress[payout_id] = {
            "total": 0, "completed": 0, "failed": 0,
            "energy_phase": False, "transfer_phase": False,
            "current_step": "", "started_at": None,
        }
    _payout_progress[payout_id].update(kwargs)


# ─── 入口 ──────────────────────────────────────────────

async def execute_payout(payout_id: int):
    """打款批次执行入口（后台任务）"""
    try:
        _update_progress(payout_id, started_at=datetime.now(timezone.utc).isoformat())
        await _do_execute(payout_id)
    except Exception as e:
        logger.error("打款 #%d 执行异常: %s", payout_id, e, exc_info=True)
        _update_progress(payout_id, current_step=f"异常终止: {str(e)[:100]}")
        try:
            async with AsyncSessionLocal() as db:
                payout = (await db.execute(
                    select(Payout).where(Payout.id == payout_id)
                )).scalar_one_or_none()
                if payout and payout.status == "executing":
                    payout.status = "failed"
                    await db.commit()
        except Exception:
            pass


async def _do_execute(payout_id: int):
    async with AsyncSessionLocal() as db:
        # ── 1. 加载批次 ──────────────────────────────────
        payout = (await db.execute(
            select(Payout).where(Payout.id == payout_id)
        )).scalar_one_or_none()
        if not payout:
            logger.error("打款 #%d 不存在", payout_id)
            return

        items = (await db.execute(
            select(PayoutItem).where(
                PayoutItem.payout_id == payout_id
            ).order_by(PayoutItem.id)
        )).scalars().all()

        # 幂等：跳过已完成
        pending_items = [it for it in items if it.status not in ("completed",)]
        if not pending_items:
            logger.info("打款 #%d 所有明细已完成，跳过", payout_id)
            return

        chain = payout.chain
        asset_type = payout.asset_type

        _update_progress(payout_id, total=len(pending_items))

        # ── 2. 加载打款钱包私钥 ───────────────────────────
        wallet = (await db.execute(
            select(Wallet).where(Wallet.id == payout.wallet_id)
        )).scalar_one_or_none()
        if not wallet or wallet.derive_index is None:
            logger.error("打款 #%d 钱包不存在或无 derive_index", payout_id)
            payout.status = "failed"
            await db.commit()
            return

        payout_private_key = get_private_key(chain, wallet.derive_index)
        from_address = wallet.address

        payout.status = "executing"
        await db.commit()

        # ── 2.5 TRON 中转钱包：等待资金到账（多签 tx 需 ~3s 确认）──
        if chain == "TRON":
            total_needed = sum(it.amount for it in pending_items)
            _update_progress(payout_id, current_step="等待资金到账")
            for attempt in range(30):  # 最多等 90s
                try:
                    if asset_type == "usdt":
                        balance = await chain_client.get_usdt_balance("TRON", from_address)
                    else:
                        balance = await chain_client.get_native_balance("TRON", from_address)
                    if balance >= total_needed:
                        logger.info("打款 #%d 资金已到账: %s (需 %s)", payout_id, balance, total_needed)
                        break
                    logger.info("打款 #%d 等待资金到账 (%d/30): 当前 %s, 需 %s", payout_id, attempt + 1, balance, total_needed)
                except Exception as e:
                    logger.warning("打款 #%d 余额查询失败: %s", payout_id, e)
                await asyncio.sleep(3)
            else:
                # 超时说明多签→中转钱包的 tx 链上未成功（Transaction Revert 等），停止打款避免发出 0 余额的无效交易
                logger.error("打款 #%d 等待资金到账超时（90s），多签转账可能链上失败，终止打款", payout_id)
                async with AsyncSessionLocal() as _fail_db:
                    _p = (await _fail_db.execute(select(Payout).where(Payout.id == payout_id))).scalar_one_or_none()
                    if _p:
                        _p.status = "failed"
                        # 将所有 pending/processing items 标记为 failed
                        _items = (await _fail_db.execute(
                            select(PayoutItem).where(
                                PayoutItem.payout_id == payout_id,
                                PayoutItem.status.in_(["pending", "processing"]),
                            )
                        )).scalars().all()
                        for _it in _items:
                            _it.status = "failed"
                            _it.error_message = "多签转账未到账（链上可能失败），请重新发起提案"
                        await _fail_db.commit()
                return

        # ── 2.6 TRON 补 TRX 到中转钱包（按实际余额差值补足）──
        if chain == "TRON":
            _update_progress(payout_id, current_step="检查 TRX 余额")
            try:
                async with AsyncSessionLocal() as gas_db:
                    tron_gas_wallet = (await gas_db.execute(
                        select(Wallet).where(Wallet.chain == "TRON", Wallet.type == "gas")
                    )).scalars().first()

                if tron_gas_wallet and tron_gas_wallet.derive_index is not None:
                    fee_buffer = GAS_ESTIMATE_TRON * GAS_BUFFER_MULTIPLIER * len(pending_items)
                    if asset_type == "native":
                        # native TRX 打款：中转钱包要同时覆盖「发出的 TRX 总量 + 带宽手续费」
                        trx_needed = sum(it.amount for it in pending_items) + fee_buffer
                    else:
                        # USDT 打款：只需带宽手续费（USDT 本身不消耗 TRX 余额）
                        trx_needed = fee_buffer
                    current_trx = await chain_client.get_native_balance("TRON", from_address)
                    if current_trx < trx_needed:
                        deficit = trx_needed - current_trx
                        gas_private_key = get_private_key("TRON", tron_gas_wallet.derive_index)
                        await chain_client.send_native(
                            "TRON", gas_private_key, tron_gas_wallet.address,
                            from_address, deficit,
                        )
                        logger.info("打款 #%d 补 TRX: %s TRX → %s (当前 %s, 需 %s)",
                                    payout_id, deficit, from_address[:10], current_trx, trx_needed)
                        await asyncio.sleep(3)  # 等待 TRX 到账
                    else:
                        logger.info("打款 #%d TRX 余额充足: %s (需 %s)", payout_id, current_trx, trx_needed)
                else:
                    logger.warning("打款 #%d 未找到 TRON Gas 钱包，跳过 TRX 补充", payout_id)
            except Exception as e:
                logger.warning("打款 #%d 补 TRX 失败（不阻断打款）: %s", payout_id, e)

        # ── 3. TRON USDT 能量预租赁（串行，2s 间隔）────────
        # energy_ok_ids: 能量充足或租赁成功的 item id 集合；不在集合内的 item 跳过转账
        energy_ok_ids: set[int] = set()
        if chain == "TRON" and asset_type == "usdt":
            _update_progress(payout_id, energy_phase=True, current_step="租赁能量")
            try:
                s = await chain_client._load_settings()
                if s.tron_energy_rental_enabled and s.tron_energy_rental_api_url:
                    api_urls = s.tron_api_urls or []
                    api_keys_list = s.tron_api_keys or []
                    rental_api_url = (s.tron_energy_rental_api_url or "").strip()
                    rental_api_key = (s.tron_energy_rental_api_key or "").strip()
                    rental_max_price = s.tron_energy_rental_max_price or 150
                    rental_duration = s.tron_energy_rental_duration or 3_600_000

                    usdt_contract = (s.tron_usdt_contract or "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").strip()
                    energy_items = [it for it in pending_items if it.status in ("pending", "processing")]
                    for i, item in enumerate(energy_items):
                        try:
                            # 优先用 feee.io estimate_energy 接口（更准确），失败降级本地模拟
                            feee_est = await tron_energy_service.estimate_fee(
                                rental_api_url, rental_api_key,
                                from_address, item.to_address, usdt_contract,
                            )
                            if feee_est and feee_est.get("energy_used", 0) > 10_000:
                                estimated = feee_est["energy_used"]
                                logger.info("[能量 %d/%d] feee.io 估算: %d energy (%.4f TRX)",
                                            i + 1, len(energy_items), estimated, feee_est.get("fee", 0))
                                # feee.io 对"从未收过 USDT 的全新地址"会低估（返回 ~64k，实际需 ~130k）
                                # 检测方法：feee.io < 100k 且目标地址 USDT=0 → 新地址存储槽，硬编码 130,285
                                # 若 feee.io < 100k 且目标 USDT > 0 → feee.io 准确，直接用
                                if estimated < 100_000:
                                    try:
                                        to_usdt_bal = await chain_client.get_usdt_balance("TRON", item.to_address)
                                    except Exception:
                                        to_usdt_bal = Decimal(0)
                                    if to_usdt_bal == 0:
                                        estimated = 130_285
                                        logger.info("[能量 %d/%d] 新地址(USDT=0)，修正为 %d energy",
                                                    i + 1, len(energy_items), estimated)
                                    # else: feee.io 值准确，保持不变
                            else:
                                # feee.io 不可用，降级到链上模拟（内含余额=0→160k保护）
                                estimated = await estimate_transfer_energy(
                                    s.tron_api_urls or [], s.tron_api_keys or [],
                                    from_address, item.to_address,
                                    int(item.amount * 1_000_000),
                                    usdt_contract,
                                )
                                logger.info("[能量 %d/%d] 本地模拟估算: %d energy", i + 1, len(energy_items), estimated)
                            result = await tron_energy_service.ensure_energy(
                                api_urls, api_keys_list, from_address,
                                energy_needed=estimated,
                                rental_enabled=True,
                                rental_api_url=rental_api_url,
                                rental_api_key=rental_api_key,
                                rental_max_price_sun=rental_max_price,
                                rental_duration_ms=rental_duration,
                            )
                            if result.get("sufficient"):
                                if result.get("rented"):
                                    logger.info("[能量 %d/%d] 租赁成功并已到账 (%d energy)", i + 1, len(energy_items), estimated)
                                else:
                                    logger.info("[能量 %d/%d] 能量充足，无需租赁", i + 1, len(energy_items))
                                energy_ok_ids.add(item.id)
                            else:
                                logger.warning("[能量 %d/%d] 能量不足，跳过此笔转账: %s", i + 1, len(energy_items), result.get("error"))
                        except Exception as e:
                            logger.warning("[能量 %d/%d] 能量租赁异常，跳过此笔转账: %s", i + 1, len(energy_items), e)

                        if i < len(energy_items) - 1:
                            await asyncio.sleep(2)  # feee.io 限频

                    _update_progress(payout_id, energy_phase=False, current_step="能量租赁完成")
                    logger.info("TRON 能量预租赁完成: %d/%d 成功", len(energy_ok_ids), len(energy_items))
                else:
                    # 未启用租赁：所有 item 直接放行（依赖地址已质押能量）
                    energy_ok_ids = {it.id for it in pending_items}
            except Exception as e:
                logger.warning("TRON 能量预租赁异常: %s", e)
                # 异常时保守处理：不放行任何 item，全部标记失败
        else:
            # 非 TRON USDT：无能量限制，全部放行
            energy_ok_ids = {it.id for it in pending_items}

        # ── 4. 串行转账（同一私钥，nonce 顺序）──────────────
        _update_progress(payout_id, transfer_phase=True, current_step="转账中")
        completed_count = 0
        failed_count = 0
        completed_amount = Decimal(0)
        nonce_cache: dict = {}

        for i, item in enumerate(pending_items):
            if item.status == "completed":
                completed_count += 1
                completed_amount += item.amount
                continue

            _update_progress(payout_id, current_step=f"转账: {i + 1}/{len(pending_items)}")

            async with AsyncSessionLocal() as item_db:
                try:
                    item_row = (await item_db.execute(
                        select(PayoutItem).where(PayoutItem.id == item.id)
                    )).scalar_one()

                    # TRON USDT：能量不足时跳过，避免烧大量 TRX
                    if chain == "TRON" and asset_type == "usdt" and item.id not in energy_ok_ids:
                        item_row.status = "failed"
                        item_row.error_message = "能量租赁失败，跳过转账以避免 TRX 浪费"
                        await item_db.commit()
                        failed_count += 1
                        logger.warning("[打款 %d/%d] 跳过（能量不足）: %s",
                                       i + 1, len(pending_items), item_row.to_address[:10])
                        continue

                    item_row.status = "processing"
                    await item_db.flush()

                    if asset_type == "native":
                        tx_hash = await chain_client.send_native(
                            chain, payout_private_key, from_address,
                            item_row.to_address, item_row.amount,
                            nonce_cache=nonce_cache,
                        )
                    else:
                        tx_hash = await chain_client.send_usdt(
                            chain, payout_private_key, from_address,
                            item_row.to_address, item_row.amount,
                            nonce_cache=nonce_cache,
                            skip_energy_rental=(chain == "TRON"),  # 已预租赁
                        )

                    item_row.tx_hash = tx_hash
                    await item_db.flush()

                    # TRON：广播成功 ≠ 执行成功，需等链上确认 receipt.result
                    if chain == "TRON":
                        _s = await chain_client._load_settings()
                        _api_url = (_s.tron_api_urls or ["https://api.trongrid.io"])[0]
                        _api_keys = _s.tron_api_keys or []
                        _headers = {"Content-Type": "application/json"}
                        if _api_keys:
                            _headers["TRON-PRO-API-KEY"] = _api_keys[0]

                        await asyncio.sleep(4)
                        for _attempt in range(10):
                            try:
                                async with httpx.AsyncClient(timeout=10) as _c:
                                    _r = await _c.post(
                                        f"{_api_url.rstrip('/')}/wallet/gettransactioninfobyid",
                                        json={"value": tx_hash},
                                        headers=_headers,
                                    )
                                    if _r.status_code == 200:
                                        _info = _r.json()
                                        if _info:
                                            _receipt = _info.get("receipt", {})
                                            _result = _receipt.get("result", "")
                                            if _result in ("FAILED", "REVERT", "OUT_OF_ENERGY", "OUT_OF_TIME"):
                                                raise RuntimeError(
                                                    f"链上执行失败: {_result} "
                                                    f"(energy={_receipt.get('energy_usage_total', '?')}) "
                                                    f"txid={tx_hash}"
                                                )
                                            if _result == "SUCCESS" or _result == "":
                                                break  # 成功或 TRX 转账（无 result 字段）
                            except RuntimeError:
                                raise
                            except Exception as _ve:
                                logger.warning("[打款 %d/%d] 链上确认查询失败 (attempt %d): %s",
                                               i + 1, len(pending_items), _attempt + 1, _ve)
                            await asyncio.sleep(5)

                    item_row.status = "completed"
                    await item_db.commit()

                    completed_count += 1
                    completed_amount += item_row.amount
                    logger.info("[打款 %d/%d] 成功: %s → %s (tx: %s)",
                                i + 1, len(pending_items),
                                item_row.to_address[:10], item_row.amount,
                                tx_hash[:16] if tx_hash else "")

                except Exception as e:
                    item_row.status = "failed"
                    item_row.error_message = str(e)[:500]
                    item_row.retry_count += 1
                    await item_db.commit()
                    failed_count += 1
                    logger.error("[打款 %d/%d] 失败 (%s): %s",
                                 i + 1, len(pending_items), item_row.to_address[:10], e)

            _update_progress(payout_id, completed=completed_count, failed=failed_count)

            # BSC: 批次间短暂等待，避免 mempool 拥堵
            if chain == "BSC" and i < len(pending_items) - 1:
                await asyncio.sleep(0.5)

        # ── 5. 更新批次状态 ───────────────────────────────
        async with AsyncSessionLocal() as db2:
            payout2 = (await db2.execute(
                select(Payout).where(Payout.id == payout_id)
            )).scalar_one()

            payout2.executed_at = datetime.now(timezone.utc)
            payout2.total_amount = completed_amount
            payout2.item_count = len(items)

            if failed_count == 0:
                payout2.status = "completed"
            elif completed_count == 0:
                payout2.status = "failed"
            else:
                payout2.status = "partial"

            await db2.commit()

        _update_progress(
            payout_id,
            completed=completed_count,
            failed=failed_count,
            transfer_phase=False,
            current_step="完成" if failed_count == 0 else f"完成(失败 {failed_count} 个)",
        )

        # ── 6. TG 通知 ────────────────────────────────────
        try:
            status_text = "全部成功" if failed_count == 0 else f"部分完成(失败 {failed_count})"
            await notifier.notify_payout_completed(
                chain=chain,
                to_address=f"{len(items)} 笔",
                amount=completed_amount,
                tx_hash="",
                memo=status_text,
            )
        except Exception as e:
            logger.warning("打款通知发送失败: %s", e)

        logger.info("打款 #%d 完成: 成功 %d, 失败 %d, 共 %s",
                    payout_id, completed_count, failed_count, completed_amount)


