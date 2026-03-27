"""
多签钱包 API — 创建 / 导入 / 激活 / 验证
"""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func as sa_func

from app.database import get_db, AsyncSessionLocal
from app.models.admin import Admin
from app.models.wallet import Wallet
from app.models.deposit_address import DepositAddress
from app.models.audit_log import AuditLog
from app.core.deps import require_module
from app.core.hdwallet import generate_addresses, get_private_key
from app.services.multisig_service import multisig_service
from app.services.chain_client import chain_client
from app.schemas.multisig_wallet import (
    MultisigWalletCreate, MultisigWalletImport,
    MultisigWalletOut, SignerInfo, VerifyResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/multisig-wallets", tags=["多签钱包"])

CanManageWallets = Depends(require_module("wallet_config"))


# ─── GET /signers ──────────────────────────────────────

@router.get("/signers", response_model=list[SignerInfo])
async def list_signers(
    chain: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """返回有签名人地址的管理员列表"""
    result = await db.execute(
        select(Admin).where(Admin.is_active == True)
    )
    admins = result.scalars().all()

    field = "signer_address_bsc" if chain == "BSC" else "signer_address_tron"
    return [
        SignerInfo(
            admin_id=a.id,
            username=a.username,
            address=getattr(a, field),
        )
        for a in admins
        if getattr(a, field)
    ]


# ─── POST /create ──────────────────────────────────────

@router.post("/create", response_model=MultisigWalletOut, status_code=201)
async def create_multisig_wallet(
    body: MultisigWalletCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """创建多签钱包（BSC: 部署 Safe / TRON: 生成地址待激活）"""

    # 1. 解析 owners
    owner_addresses = await _resolve_owners(db, body.chain, body.owners)

    if len(owner_addresses) == 0:
        raise HTTPException(status_code=400, detail="至少需要 1 个签名人")
    if body.threshold <= 0:
        raise HTTPException(status_code=400, detail="threshold 至少为 1")
    if body.threshold > len(owner_addresses):
        raise HTTPException(status_code=400, detail="threshold 不能大于 owners 数量")

    if body.chain == "BSC":
        return await _create_bsc_safe(
            db, request, current_user, body, owner_addresses,
        )
    else:
        return await _create_tron_multisig(
            db, request, current_user, body, owner_addresses,
        )


async def _resolve_owners(db: AsyncSession, chain: str, owner_inputs) -> list[str]:
    """解析 OwnerInput 列表为地址列表"""
    addresses = []
    field = "signer_address_bsc" if chain == "BSC" else "signer_address_tron"

    for inp in owner_inputs:
        if inp.admin_id:
            admin = (await db.execute(
                select(Admin).where(Admin.id == inp.admin_id)
            )).scalar_one_or_none()
            if not admin:
                raise HTTPException(status_code=404, detail=f"管理员 {inp.admin_id} 不存在")
            addr = getattr(admin, field)
            if not addr:
                raise HTTPException(
                    status_code=400,
                    detail=f"管理员 {admin.username} 未设置 {chain} 签名地址",
                )
            addresses.append(addr)
        else:
            addr = inp.address
            if chain == "BSC":
                if not (addr and addr.startswith("0x") and len(addr) == 42):
                    raise HTTPException(status_code=400, detail=f"BSC 地址格式无效: {addr}")
            else:
                if not (addr and addr.startswith("T") and len(addr) == 34):
                    raise HTTPException(status_code=400, detail=f"TRON 地址格式无效: {addr}")
            addresses.append(addr)

    # 去重检查
    if len(set(addresses)) != len(addresses):
        raise HTTPException(status_code=400, detail="签名人地址不能重复")

    return addresses


async def _create_bsc_safe(db, request, current_user, body, owner_addresses):
    """BSC: 选 gas 钱包 → 先入库(deploying) → 后台异步部署 Safe"""

    # 选 gas 钱包
    if body.gas_wallet_id:
        gas_wallet = (await db.execute(
            select(Wallet).where(Wallet.id == body.gas_wallet_id)
        )).scalar_one_or_none()
        if not gas_wallet or gas_wallet.type != "gas" or gas_wallet.chain != "BSC":
            raise HTTPException(status_code=400, detail="指定的 BSC Gas 钱包无效")
    else:
        # 自动选余额最高的
        gas_wallets = (await db.execute(
            select(Wallet).where(Wallet.chain == "BSC", Wallet.type == "gas")
        )).scalars().all()
        if not gas_wallets:
            raise HTTPException(status_code=400, detail="BSC 无可用 Gas 钱包")

        best_wallet = None
        best_balance = -1
        for gw in gas_wallets:
            if not gw.address or gw.derive_index is None:
                continue
            try:
                bal = await chain_client.get_native_balance("BSC", gw.address)
                if bal > best_balance:
                    best_balance = bal
                    best_wallet = gw
            except Exception:
                continue
        if not best_wallet:
            raise HTTPException(status_code=400, detail="BSC 无可用 Gas 钱包")
        gas_wallet = best_wallet

    gas_derive_index = gas_wallet.derive_index

    # 先入库 deploying 状态
    label = body.label or f"BSC Safe {body.type} ({body.threshold}/{len(owner_addresses)})"
    wallet = Wallet(
        chain="BSC",
        type=body.type,
        address=None,  # 部署完成后填入
        label=label,
        is_multisig=True,
        owners=owner_addresses,
        threshold=body.threshold,
        multisig_status="deploying",
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    wallet_id = wallet.id

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="create_multisig_wallet",
        detail=f"发起 BSC Safe 部署 ({body.threshold}/{len(owner_addresses)})，后台执行中",
        ip_address=request.client.host if request.client else None,
    ))

    # 后台异步部署
    asyncio.create_task(_deploy_bsc_safe_bg(
        wallet_id, owner_addresses, body.threshold, gas_derive_index,
    ))

    return MultisigWalletOut.model_validate(wallet)


async def _deploy_bsc_safe_bg(
    wallet_id: int,
    owner_addresses: list[str],
    threshold: int,
    gas_derive_index: int,
):
    """后台任务：部署 Safe 并更新钱包记录"""
    try:
        gas_key = get_private_key("BSC", gas_derive_index)
        safe_address, tx_hash = await multisig_service.deploy_bsc_safe(
            owner_addresses, threshold, gas_key,
        )
        async with AsyncSessionLocal() as session:
            wallet = (await session.execute(
                select(Wallet).where(Wallet.id == wallet_id)
            )).scalar_one()
            wallet.address = safe_address
            wallet.deployment_tx = tx_hash
            wallet.multisig_status = "active"
            await session.commit()
        logger.info(f"BSC Safe 部署成功: wallet_id={wallet_id}, address={safe_address}")
    except Exception as e:
        logger.error(f"BSC Safe 部署失败: wallet_id={wallet_id}, error={e}")
        try:
            async with AsyncSessionLocal() as session:
                wallet = (await session.execute(
                    select(Wallet).where(Wallet.id == wallet_id)
                )).scalar_one()
                wallet.multisig_status = "failed"
                wallet.label = (wallet.label or "") + f" [部署失败: {e}]"
                await session.commit()
        except Exception as e2:
            logger.error(f"更新失败状态出错: {e2}")


async def _create_tron_multisig(db, request, current_user, body, owner_addresses):
    """TRON: 部署 TronMultiSig 合约 → 入库(deploying) → 后台自动部署"""
    from app.models.system_settings import SystemSettings
    from app.services.multisig_service import TRON_DEPLOY_ENERGY
    from app.services.tron_energy import tron_energy_service
    from decimal import Decimal

    # 预检：feee.io 余额是否足够支付部署能量租赁费用
    settings = (await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )).scalar_one_or_none()

    if not settings or not settings.tron_energy_rental_api_url or not settings.tron_energy_rental_api_key:
        raise HTTPException(status_code=400, detail="未配置 feee.io 能量租赁 API，无法部署 TRON 合约")

    rental_max_price = settings.tron_energy_rental_max_price or 420
    feee_balance = await tron_energy_service.get_feee_balance(
        settings.tron_energy_rental_api_url,
        settings.tron_energy_rental_api_key,
    )

    # 用实际市价估算（通过 estimate_energy 接口反推当前单价），加 20% 安全余量
    # 若查询失败则降级用 max_price
    actual_price_sun = rental_max_price
    try:
        est = await tron_energy_service.estimate_fee(
            settings.tron_energy_rental_api_url,
            settings.tron_energy_rental_api_key,
            from_address="TSQL23k3ve3G453DNJqnQYM2oosGgsddTZ",  # 随便一个 TRON 地址
            to_address="TSQL23k3ve3G453DNJqnQYM2oosGgsddTZ",
        )
        if est and est["energy_used"] > 0:
            actual_price_sun = int(est["fee"] * 1_000_000 / est["energy_used"])
    except Exception:
        pass

    price_with_buffer = min(actual_price_sun, rental_max_price) * Decimal("1.2")
    estimated_cost = Decimal(TRON_DEPLOY_ENERGY) * price_with_buffer / Decimal(1_000_000)
    if feee_balance is None or feee_balance < estimated_cost:
        raise HTTPException(
            status_code=400,
            detail=f"feee.io 余额不足，部署需约 {estimated_cost:.2f} TRX（当前市价估算+20%），当前余额 {feee_balance or 0:.2f} TRX",
        )

    label = body.label or f"TRON 多签 {body.type} ({body.threshold}/{len(owner_addresses)})"

    # 合约钱包不占 HD 索引（合约地址由 gas 钱包 + nonce 决定）
    # 但中转钱包需要 HD 索引，先分配
    relay_wallet_id = None
    if body.type == "payout":
        max_gas = (await db.execute(
            select(sa_func.max(Wallet.derive_index)).where(
                Wallet.chain == "TRON", Wallet.type == "gas",
            )
        )).scalar() or -1
        max_addr = (await db.execute(
            select(sa_func.max(DepositAddress.derive_index)).where(
                DepositAddress.chain == "TRON",
            )
        )).scalar() or -1
        max_wallet = (await db.execute(
            select(sa_func.max(Wallet.derive_index)).where(
                Wallet.chain == "TRON", Wallet.derive_index.isnot(None),
            )
        )).scalar() or -1
        relay_index = max(max_gas, max_addr, max_wallet) + 1

        try:
            relay_derived = generate_addresses("TRON", relay_index, 1)
            relay_address = relay_derived[0][1]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"中转钱包地址派生失败: {e}")

        relay_wallet = Wallet(
            chain="TRON",
            type="payout",
            address=relay_address,
            label=f"{label} [中转]",
            derive_index=relay_index,
            is_multisig=False,
        )
        db.add(relay_wallet)
        await db.flush()
        await db.refresh(relay_wallet)
        relay_wallet_id = relay_wallet.id

        db.add(AuditLog(
            admin_id=current_user.id,
            admin_username=current_user.username,
            action="create_relay_wallet",
            detail=f"自动创建 TRON 中转钱包 #{relay_wallet.id}: {relay_address}",
            ip_address=request.client.host if request.client else None,
        ))

    # 合约钱包入库，地址部署后填入
    wallet = Wallet(
        chain="TRON",
        type=body.type,
        address=None,
        label=label,
        derive_index=None,  # 合约无 HD 索引
        is_multisig=True,
        owners=owner_addresses,
        threshold=body.threshold,
        multisig_status="deploying",
        relay_wallet_id=relay_wallet_id,
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    wallet_id = wallet.id

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="create_multisig_wallet",
        detail=f"发起 TRON 合约多签部署 ({body.threshold}/{len(owner_addresses)})，后台执行中",
        ip_address=request.client.host if request.client else None,
    ))

    # 后台部署
    asyncio.create_task(_deploy_tron_contract_bg(
        wallet_id, owner_addresses, body.threshold,
    ))

    return MultisigWalletOut.model_validate(wallet)


async def _deploy_tron_contract_bg(
    wallet_id: int,
    owner_addresses: list[str],
    threshold: int,
):
    """后台任务：部署 TronMultiSig 合约并更新钱包记录"""
    from app.models.system_settings import SystemSettings
    from app.database import AsyncSessionLocal

    try:
        # 加载配置
        async with AsyncSessionLocal() as session:
            settings = (await session.execute(
                select(SystemSettings).where(SystemSettings.id == 1)
            )).scalar_one()
            rental_api_url = settings.tron_energy_rental_api_url or ""
            rental_api_key = settings.tron_energy_rental_api_key or ""
            rental_max_price = settings.tron_energy_rental_max_price or 420

            # 自动选 TRON gas 钱包（余额最高）
            gas_wallets = (await session.execute(
                select(Wallet).where(
                    Wallet.chain == "TRON",
                    Wallet.type == "gas",
                    Wallet.derive_index.isnot(None),
                )
            )).scalars().all()

        if not gas_wallets:
            raise RuntimeError("无可用 TRON Gas 钱包")

        best_wallet = None
        best_balance = -1
        for gw in gas_wallets:
            try:
                bal = await chain_client.get_native_balance("TRON", gw.address)
                if bal > best_balance:
                    best_balance = bal
                    best_wallet = gw
            except Exception:
                continue
        if not best_wallet:
            raise RuntimeError("TRON Gas 钱包余额查询失败")

        gas_key = get_private_key("TRON", best_wallet.derive_index)

        if not rental_api_url or not rental_api_key:
            raise RuntimeError("未配置 feee.io 能量租赁 API，无法部署 TRON 合约（需要强制租能量）")

        contract_address, tx_hash = await multisig_service.deploy_tron_contract(
            owners=owner_addresses,
            threshold=threshold,
            gas_wallet_address=best_wallet.address,
            gas_wallet_private_key=gas_key,
            rental_api_url=rental_api_url,
            rental_api_key=rental_api_key,
            rental_max_price_sun=rental_max_price,
        )

        async with AsyncSessionLocal() as session:
            wallet = (await session.execute(
                select(Wallet).where(Wallet.id == wallet_id)
            )).scalar_one()
            wallet.address = contract_address
            wallet.deployment_tx = tx_hash
            wallet.multisig_status = "active"
            await session.commit()

        logger.info("TRON 合约多签部署成功: wallet_id=%d, address=%s", wallet_id, contract_address)

    except Exception as e:
        logger.error("TRON 合约多签部署失败: wallet_id=%d, error=%s", wallet_id, e)
        try:
            async with AsyncSessionLocal() as session:
                wallet = (await session.execute(
                    select(Wallet).where(Wallet.id == wallet_id)
                )).scalar_one()
                wallet.multisig_status = "failed"
                wallet.label = (wallet.label or "") + f" [部署失败: {e}]"
                await session.commit()
        except Exception as e2:
            logger.error("更新失败状态出错: %s", e2)


# ─── POST /import ──────────────────────────────────────

@router.post("/import", response_model=MultisigWalletOut, status_code=201)
async def import_multisig_wallet(
    body: MultisigWalletImport,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """导入已有多签钱包（链上验证 owners/threshold）"""

    # 检查重复
    existing = (await db.execute(
        select(Wallet).where(
            Wallet.chain == body.chain,
            Wallet.address == body.address,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="该地址已存在")

    # 链上验证
    try:
        if body.chain == "BSC":
            result = await multisig_service.verify_bsc_safe(body.address)
        else:
            result = await _verify_tron_wallet(body.address)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"链上验证失败: {e}")

    label = body.label or f"{body.chain} 多签 {body.type} ({result['threshold']}/{len(result['owners'])})"
    wallet = Wallet(
        chain=body.chain,
        type=body.type,
        address=body.address,
        label=label,
        is_multisig=True,
        owners=result["owners"],
        threshold=result["threshold"],
        multisig_status="active",
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="import_multisig_wallet",
        detail=f"导入 {body.chain} 多签 {body.type}: {body.address} ({result['threshold']}/{len(result['owners'])})",
        ip_address=request.client.host if request.client else None,
    ))

    return MultisigWalletOut.model_validate(wallet)


# ─── POST /{id}/verify ────────────────────────────────

@router.post("/{wallet_id}/verify", response_model=VerifyResult)
async def verify_multisig_wallet(
    wallet_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """重新从链上读取 owners/threshold 并更新"""
    wallet = (await db.execute(
        select(Wallet).where(Wallet.id == wallet_id)
    )).scalar_one_or_none()

    if not wallet:
        raise HTTPException(status_code=404, detail="钱包不存在")
    if not wallet.is_multisig:
        raise HTTPException(status_code=400, detail="非多签钱包")
    if not wallet.address:
        raise HTTPException(status_code=400, detail="钱包地址为空")

    try:
        if wallet.chain == "BSC":
            result = await multisig_service.verify_bsc_safe(wallet.address)
        else:
            result = await _verify_tron_wallet(wallet.address)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"链上验证失败: {e}")

    # 更新数据库
    wallet.owners = result["owners"]
    wallet.threshold = result["threshold"]
    await db.flush()

    return VerifyResult(owners=result["owners"], threshold=result["threshold"])


async def _verify_tron_wallet(address: str) -> dict:
    """
    自动判断 TRON 钱包类型（合约多签 or 原生多签）并验证。
    优先尝试合约验证，失败则降级到原生多签验证。
    """
    try:
        return await multisig_service.verify_tron_contract(address)
    except Exception:
        pass
    return await multisig_service.verify_tron_multisig(address)
