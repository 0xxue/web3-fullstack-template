import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.admin import Admin
from app.models.wallet import Wallet
from app.models.audit_log import AuditLog
from app.core.deps import require_module
from app.core.hdwallet import generate_addresses, get_private_key
from app.schemas.wallet import WalletOut, WalletWithBalance, WalletCreate, WalletUpdate
from app.services.chain_client import chain_client
from app.services.tron_energy import tron_energy_service
from app.models.system_settings import SystemSettings
from sqlalchemy import func as sa_func

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/wallets", tags=["钱包配置"])

CanManageWallets = Depends(require_module("wallet_config"))


# ─── GET /settings/wallets ────────────────────────────

@router.get("", response_model=list[WalletOut])
async def get_wallets(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """列出所有钱包"""
    result = await db.execute(
        select(Wallet).where(Wallet.is_active == True).order_by(Wallet.chain, Wallet.type, Wallet.id)
    )
    wallets = result.scalars().all()
    return [WalletOut.model_validate(w) for w in wallets]


# ─── GET /settings/wallets/balances ───────────────────

@router.get("/balances", response_model=list[WalletWithBalance])
async def get_wallets_with_balances(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
    types: str | None = None,  # 逗号分隔，如 "collection,gas"
):
    """列出钱包 + 链上余额（批量查询）。types 可过滤钱包类型，提升速度。"""
    query = select(Wallet).where(Wallet.is_active == True).order_by(Wallet.chain, Wallet.type, Wallet.id)
    if types:
        type_list = [t.strip() for t in types.split(",") if t.strip()]
        if type_list:
            query = query.where(Wallet.type.in_(type_list))
    result = await db.execute(query)
    wallets = list(result.scalars().all())

    # 按链分组，批量查余额
    chain_groups: dict[str, list[Wallet]] = {}
    for w in wallets:
        if w.address:
            chain_groups.setdefault(w.chain, []).append(w)

    # 批量查询各链余额（各链并行）
    balance_map: dict[str, dict] = {}  # address -> {usdt, native}

    async def _fetch_chain(chain: str, group: list):
        try:
            addrs = [w.address for w in group]
            return await chain_client.batch_get_balances(chain, addrs)
        except Exception as e:
            logger.warning("批量查询 %s 钱包余额失败: %s", chain, e)
            return []

    results = await asyncio.gather(*[
        _fetch_chain(chain, group) for chain, group in chain_groups.items()
    ])
    for batch_results in results:
        for br in batch_results:
            balance_map[br["address"]] = br

    output = []
    for w in wallets:
        out = WalletWithBalance.model_validate(w)
        if w.address and w.address in balance_map:
            bal = balance_map[w.address]
            out.native_balance = bal.get("native")
            out.usdt_balance = bal.get("usdt")
        output.append(out)
    return output


# ─── GET /settings/wallets/feee-balance ────────────────

@router.get("/feee-balance")
async def get_feee_balance(
    db: Annotated[AsyncSession, Depends(get_db)],
    _current_user: Annotated[Admin, CanManageWallets],
):
    """查询 feee.io 能量租赁账户 TRX 余额"""
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings or not settings.tron_energy_rental_enabled:
        return {"balance": None, "enabled": False}

    api_url = settings.tron_energy_rental_api_url
    api_key = settings.tron_energy_rental_api_key
    if not api_url or not api_key:
        return {"balance": None, "enabled": True, "error": "未配置 API"}

    balance = await tron_energy_service.get_feee_balance(api_url, api_key)
    return {"balance": str(balance) if balance is not None else None, "enabled": True}


# ─── POST /settings/wallets ──────────────────────────

@router.post("", response_model=WalletOut, status_code=201)
async def create_wallet(
    body: WalletCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """新建钱包"""

    if body.type == "gas":
        # Gas 钱包：自动分配 derive_index
        if body.derive_index is not None:
            # 手动指定时检查重复
            next_index = body.derive_index
            existing = (await db.execute(
                select(Wallet).where(
                    Wallet.chain == body.chain,
                    Wallet.type == "gas",
                    Wallet.derive_index == next_index,
                )
            )).scalar_one_or_none()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"{body.chain} 已存在 derive_index={next_index} 的 Gas 钱包",
                )
        else:
            # 自动分配：取当前最大 derive_index + 1（从 gas 钱包和充值地址两边取最大值）
            from app.models.deposit_address import DepositAddress
            max_gas = (await db.execute(
                select(sa_func.max(Wallet.derive_index)).where(
                    Wallet.chain == body.chain, Wallet.type == "gas",
                )
            )).scalar() or -1
            max_addr = (await db.execute(
                select(sa_func.max(DepositAddress.derive_index)).where(
                    DepositAddress.chain == body.chain,
                )
            )).scalar() or -1
            next_index = max(max_gas, max_addr) + 1

        # HD 派生地址
        try:
            derived = generate_addresses(body.chain, next_index, 1)
            address = derived[0][1]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"地址派生失败: {e}")

        wallet = Wallet(
            chain=body.chain,
            type="gas",
            address=address,
            label=body.label or f"{body.chain} Gas #{next_index}",
            derive_index=next_index,
        )
    else:
        # 归集 / 打款钱包：手动填 Safe 地址

        wallet = Wallet(
            chain=body.chain,
            type=body.type,
            address=body.address,
            label=body.label or f"{body.chain} {'归集' if body.type == 'collection' else '打款'}钱包",
        )

    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="create_wallet",
        detail=f"新建 {wallet.chain} {wallet.type} 钱包: {wallet.label} ({wallet.address})",
        ip_address=request.client.host if request.client else None,
    ))

    return WalletOut.model_validate(wallet)


# ─── PUT /settings/wallets/{id} ──────────────────────

@router.put("/{wallet_id}", response_model=WalletOut)
async def update_wallet(
    wallet_id: int,
    body: WalletUpdate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """修改钱包地址或标签"""
    result = await db.execute(select(Wallet).where(Wallet.id == wallet_id))
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(status_code=404, detail="钱包不存在")

    update_data = body.model_dump(exclude_unset=True)

    # 钱包地址创建后不允许修改，如需更换请删除后重新创建
    if "address" in update_data:
        raise HTTPException(status_code=400, detail="钱包地址创建后不可修改，如需更换请删除后重新创建")

    # 校验 relay_wallet_id：必须是同链的非多签 payout 钱包，并给目标钱包打 is_relay_wallet 标记
    if "relay_wallet_id" in update_data and update_data["relay_wallet_id"] is not None:
        relay = (await db.execute(
            select(Wallet).where(Wallet.id == update_data["relay_wallet_id"])
        )).scalar_one_or_none()
        if not relay:
            raise HTTPException(status_code=404, detail="中转钱包不存在")
        if relay.chain != wallet.chain or relay.is_multisig:
            raise HTTPException(status_code=400, detail="中转钱包链不匹配或不能为多签钱包")
        # 给目标钱包打中转标记（永久，不随引用关系删除而消失）
        if not relay.is_relay_wallet:
            relay.is_relay_wallet = True

    for field, value in update_data.items():
        setattr(wallet, field, value)

    await db.flush()
    await db.refresh(wallet)

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="update_wallet_config",
        detail=f"修改 {wallet.label or wallet.chain + ' ' + wallet.type}: {list(update_data.keys())}",
        ip_address=request.client.host if request.client else None,
    ))

    return WalletOut.model_validate(wallet)


# ─── DELETE /settings/wallets/{id} ────────────────────

@router.delete("/{wallet_id}", status_code=204)
async def delete_wallet(
    wallet_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """删除钱包"""
    from app.models.proposal import Proposal
    from app.models.payout import Payout

    result = await db.execute(select(Wallet).where(Wallet.id == wallet_id))
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(status_code=404, detail="钱包不存在")

    # 检查是否有进行中的提案引用此钱包
    active_proposals = (await db.execute(
        select(Proposal.id).where(
            Proposal.wallet_id == wallet_id,
            Proposal.status.in_(["pending", "signing", "executing"]),
        ).limit(1)
    )).scalar_one_or_none()
    if active_proposals:
        raise HTTPException(status_code=409, detail="该钱包有进行中的多签提案，无法删除")

    # 检查是否有进行中的打款批次引用此钱包
    active_payouts = (await db.execute(
        select(Payout.id).where(
            Payout.wallet_id == wallet_id,
            Payout.status.in_(["pending", "executing"]),
        ).limit(1)
    )).scalar_one_or_none()
    if active_payouts:
        raise HTTPException(status_code=409, detail="该钱包有进行中的打款批次，无法删除")

    # 如果此钱包是其他钱包的中转钱包，先解除关联
    await db.execute(
        update(Wallet).where(Wallet.relay_wallet_id == wallet_id).values(relay_wallet_id=None)
    )

    # 已完成/失败的历史提案解除钱包关联（wallet_id 可为 NULL）
    await db.execute(
        update(Proposal).where(Proposal.wallet_id == wallet_id).values(wallet_id=None)
    )

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="delete_wallet",
        detail=f"删除 {wallet.chain} {wallet.type} 钱包: {wallet.label} ({wallet.address})",
        ip_address=request.client.host if request.client else None,
    ))

    try:
        await db.delete(wallet)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该钱包仍被其他记录引用，无法删除")


# ─── GET /settings/wallets/{id}/export-key ────────────

@router.get("/{wallet_id}/export-key")
async def export_gas_wallet_key(
    wallet_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[Admin, CanManageWallets],
):
    """导出 Gas 钱包私钥（仅 gas 类型可导出）"""
    result = await db.execute(select(Wallet).where(Wallet.id == wallet_id))
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(status_code=404, detail="钱包不存在")

    if wallet.type != "gas":
        raise HTTPException(status_code=400, detail="仅 Gas 钱包可导出私钥")

    if wallet.derive_index is None:
        raise HTTPException(status_code=400, detail="该 Gas 钱包缺少 derive_index")

    try:
        private_key = get_private_key(wallet.chain, wallet.derive_index)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"私钥派生失败: {e}")

    db.add(AuditLog(
        admin_id=current_user.id,
        admin_username=current_user.username,
        action="export_gas_key",
        detail=f"导出 {wallet.chain} Gas 钱包私钥: {wallet.label} ({wallet.address[:10]}...)",
        ip_address=request.client.host if request.client else None,
    ))

    return {"address": wallet.address, "private_key": private_key}
