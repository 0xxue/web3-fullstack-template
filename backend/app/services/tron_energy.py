"""
TRON 能量租赁服务 — 在 TRC20 转账前自动租赁能量以降低手续费

支持第三方能量租赁平台（预付费 API 模式）：
  - 转账前检查发送地址的能量
  - 能量不足时自动调用租赁 API
  - 支持配置租赁平台 URL、API Key、单价上限

典型能量消耗：
  - TRC20 USDT transfer: ~65,000 energy
  - 不租能量: 烧 30-65 TRX
  - 租能量: 约 3-5 TRX
"""

import asyncio
import logging
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

USDT_TRANSFER_ENERGY = 80_000  # USDT transfer 所需能量（实测多签约 75,000+，留足余量）
ENERGY_BUFFER = 5_000  # 额外缓冲


class TronEnergyService:
    """TRON 能量租赁管理"""

    async def get_account_resource(
        self, api_urls: list, api_keys: list, address: str,
    ) -> dict:
        """查询地址的能量/带宽资源"""
        for i, api_url in enumerate(api_urls or []):
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_keys:
                headers["TRON-PRO-API-KEY"] = api_keys[i % len(api_keys)]
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{api_url}/wallet/getaccountresource",
                        json={"address": address, "visible": True},
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        return resp.json()
            except Exception as e:
                logger.debug("查询账户资源失败 %s: %s", api_url[:30], e)
                continue
        return {}

    def get_available_energy(self, resource: dict) -> int:
        """从 getaccountresource 响应中计算可用能量"""
        # EnergyLimit = 质押获得的总能量
        # EnergyUsed = 已使用的能量
        # 可用 = EnergyLimit - EnergyUsed
        total = resource.get("EnergyLimit", 0)
        used = resource.get("EnergyUsed", 0)
        return max(0, total - used)

    async def ensure_energy(
        self,
        api_urls: list,
        api_keys: list,
        address: str,
        energy_needed: int = USDT_TRANSFER_ENERGY,
        # 租赁配置
        rental_enabled: bool = False,
        rental_api_url: str = "",
        rental_api_key: str = "",
        rental_max_price_sun: int = 420,  # 每单位能量最高价(sun), 默认420sun≈0.00042TRX
        rental_duration_ms: int = 3_600_000,  # 租赁时长(毫秒), 默认1小时
    ) -> dict:
        """
        确保地址有足够能量执行 TRC20 转账。
        返回: {"sufficient": bool, "available": int, "needed": int, "rented": bool, "rental_tx": str|None, "error": str|None}
        """
        resource = await self.get_account_resource(api_urls, api_keys, address)
        available = self.get_available_energy(resource)
        needed = energy_needed + ENERGY_BUFFER

        result = {
            "sufficient": available >= needed,
            "available": available,
            "needed": needed,
            "rented": False,
            "rental_tx": None,
            "error": None,
        }

        if result["sufficient"]:
            logger.info(
                "地址 %s 能量充足: %d/%d", address[:10], available, needed
            )
            return result

        if not rental_enabled or not rental_api_url:
            logger.warning(
                "地址 %s 能量不足 (%d/%d)，未启用租赁，将使用 TRX 支付",
                address[:10], available, needed,
            )
            result["error"] = "能量不足且未启用租赁"
            return result

        # 租赁能量
        shortage = needed - available
        try:
            rental_result = await self._rent_energy(
                rental_api_url, rental_api_key,
                address, shortage,
                rental_max_price_sun, rental_duration_ms,
            )
            result["rented"] = True
            result["rental_tx"] = rental_result.get("tx_id") or rental_result.get("order_id")
            logger.info(
                "为 %s 租赁 %d 能量成功: %s",
                address[:10], shortage, result["rental_tx"],
            )

            # 轮询确认能量已到账（feee.io API 成功 ≠ 链上立即生效，最多等 30s）
            for attempt in range(15):
                await asyncio.sleep(2)
                resource2 = await self.get_account_resource(api_urls, api_keys, address)
                available2 = self.get_available_energy(resource2)
                if available2 >= needed:
                    logger.info(
                        "地址 %s 能量到账确认: %d >= %d (等待 %ds)",
                        address[:10], available2, needed, (attempt + 1) * 2,
                    )
                    result["sufficient"] = True
                    result["available"] = available2
                    return result
                logger.debug(
                    "地址 %s 等待能量到账 (%d/15): 当前 %d, 需 %d",
                    address[:10], attempt + 1, available2, needed,
                )

            # 超时：能量未到账，标记失败避免烧 TRX
            logger.error(
                "地址 %s 能量租赁 API 成功但链上未到账（30s 超时），需 %d 当前 %d",
                address[:10], needed, available2,
            )
            result["sufficient"] = False
            result["error"] = "能量已租赁但链上未到账（30s 超时）"

        except Exception as e:
            logger.error("能量租赁失败 (%s): %s", address[:10], e)
            result["error"] = f"租赁失败: {e}"

        return result

    async def _rent_energy(
        self,
        api_url: str,
        api_key: str,
        receiver: str,
        energy_amount: int,
        max_price_sun: int,
        duration_ms: int,
    ) -> dict:
        """
        调用 feee.io 能量租赁 API。

        POST https://feee.io/open/v2/order/submit
        Headers:
            key: <api_key>
            Content-Type: application/json
            User-Agent: python-httpx
        Body: {
            "resource_type": 1,           # 1=能量
            "receive_address": "T...",
            "resource_value": 65000,
            "rent_duration": 1,           # 租用时长
            "rent_time_unit": "h",        # d=天, h=小时, m=分钟
            "rent_time_second": 3600      # 秒数，需>600且为3的倍数
        }
        """
        # 将毫秒转换为 feee.io 的时长参数
        duration_sec = max(duration_ms // 1000, 600)
        # 确保是3的倍数
        duration_sec = (duration_sec // 3) * 3
        if duration_sec < 600:
            duration_sec = 600

        # 根据时长选择合适的单位
        if duration_sec >= 86400:
            rent_duration = duration_sec // 86400
            rent_time_unit = "d"
        elif duration_sec >= 3600:
            rent_duration = duration_sec // 3600
            rent_time_unit = "h"
        else:
            rent_duration = 10
            rent_time_unit = "m"

        headers = {
            "Content-Type": "application/json",
            "key": api_key,
            "User-Agent": "python-httpx",
        }

        payload = {
            "resource_type": 1,  # 1=能量
            "receive_address": receiver,
            "resource_value": energy_amount,
            "rent_duration": rent_duration,
            "rent_time_unit": rent_time_unit,
            "rent_time_second": duration_sec,
        }

        url = f"{api_url.rstrip('/')}/open/v2/order/submit"
        logger.info("feee.io 租赁请求: %s, payload: %s", url, payload)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"feee.io API 返回 {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            code = data.get("code", -1)
            if code != 0:
                raise RuntimeError(
                    f"feee.io 错误 (code={code}): {data.get('msg', str(data))}"
                )

            logger.info("feee.io 租赁成功: request_id=%s", data.get("request_id"))
            return data

    async def estimate_cost(
        self,
        api_urls: list,
        api_keys: list,
        address: str,
        energy_needed: int = USDT_TRANSFER_ENERGY,
        rental_max_price_sun: int = 420,
    ) -> dict:
        """
        估算转账能量成本。
        返回: {"has_energy": bool, "shortage": int, "burn_cost_trx": Decimal, "rental_cost_trx": Decimal}
        """
        resource = await self.get_account_resource(api_urls, api_keys, address)
        available = self.get_available_energy(resource)
        shortage = max(0, energy_needed - available)

        # 烧 TRX 的成本: 1 energy = 420 sun (当前网络价格，会浮动)
        # 实际链上动态价格通过 getenergyprices 获取
        burn_price_sun = 420  # 默认值
        try:
            for i, api_url in enumerate(api_urls or []):
                headers: dict[str, str] = {}
                if api_keys:
                    headers["TRON-PRO-API-KEY"] = api_keys[i % len(api_keys)]
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(
                        f"{api_url}/wallet/getenergyprices",
                        json={}, headers=headers,
                    )
                    if resp.status_code == 200:
                        prices_str = resp.json().get("prices", "")
                        if prices_str:
                            # 格式: "timestamp:price,timestamp:price,..."
                            latest = prices_str.strip().split(",")[-1]
                            burn_price_sun = int(latest.split(":")[-1])
                        break
        except Exception:
            pass

        burn_cost = Decimal(shortage * burn_price_sun) / Decimal(1_000_000)
        rental_cost = Decimal(shortage * rental_max_price_sun) / Decimal(1_000_000)

        return {
            "has_energy": shortage == 0,
            "available": available,
            "needed": energy_needed,
            "shortage": shortage,
            "burn_cost_trx": burn_cost,
            "rental_cost_trx": rental_cost,
            "burn_price_sun": burn_price_sun,
        }


    async def estimate_fee(
        self,
        rental_api_url: str,
        rental_api_key: str,
        from_address: str,
        to_address: str,
        contract_address: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    ) -> dict | None:
        """
        调用 feee.io estimate_energy 接口，返回实际预估能量和 TRX 费用。
        返回: {"energy_used": int, "fee": float} 或 None（失败时）
        """
        try:
            url = f"{rental_api_url.rstrip('/')}/open/v2/order/estimate_energy"
            params = {
                "from_address": from_address,
                "contract_address": contract_address,
                "to_address": to_address,
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params, headers={"key": rental_api_key, "User-Agent": "python-httpx"})
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        d = data.get("data", {})
                        return {"energy_used": d.get("energy_used", 0), "fee": d.get("fee", 0)}
        except Exception as e:
            logger.warning("feee.io estimate_energy 失败: %s", e)
        return None

    async def get_feee_balance(self, rental_api_url: str, rental_api_key: str) -> Decimal | None:
        """查询 feee.io 账户 TRX 余额（用于预检）"""
        try:
            url = f"{rental_api_url.rstrip('/')}/open/v2/api/query"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers={"key": rental_api_key, "Content-Type": "application/json", "User-Agent": "python-httpx"})
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        trx_money = data.get("data", {}).get("trx_money")
                        if trx_money is not None:
                            return Decimal(str(trx_money))  # feee.io 返回单位已是 TRX
        except Exception as e:
            logger.warning("查询 feee.io 账户余额失败: %s", e)
        return None


async def estimate_transfer_energy(
    api_urls: list,
    api_keys: list,
    from_address: str,
    to_address: str,
    amount_sun: int,
    usdt_contract: str,
) -> int:
    """
    估算单笔 TRON USDT 转账所需能量。

    策略：
    1. triggerconstantcontract 模拟 → sim × 1.1，min 75k，max 300k
    2. 模拟失败 → 查目标地址 USDT 余额
       余额 = 0（全新地址，需建槽）→ 160k
       有余额 → 75k
    """
    if not api_urls or not usdt_contract or not to_address:
        return 75_000

    from app.services.chain_client import (
        _tron_abi_encode_address, _tron_abi_encode_uint256,
    )

    api_url = api_urls[0].rstrip("/")
    headers: dict[str, str] = {}
    if api_keys:
        headers["TRON-PRO-API-KEY"] = api_keys[0]

    # 1. triggerconstantcontract 模拟
    try:
        param = _tron_abi_encode_address(to_address) + _tron_abi_encode_uint256(amount_sun)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{api_url}/wallet/triggerconstantcontract",
                json={
                    "owner_address": from_address,
                    "contract_address": usdt_contract,
                    "function_selector": "transfer(address,uint256)",
                    "parameter": param,
                    "visible": True,
                },
                headers=headers,
            )
            if r.status_code == 200:
                sim_e = r.json().get("energy_used", 0)
                if sim_e > 10_000:
                    estimated = max(75_000, min(300_000, int(sim_e * 1.1)))
                    logger.info(
                        "能量模拟: sim=%d, ×1.1=%d, from=%s, to=%s",
                        sim_e, estimated, from_address[:10], to_address[:10],
                    )
                    return estimated
                raise ValueError(f"模拟值异常: {sim_e}")
    except Exception as e:
        logger.warning("triggerconstantcontract 模拟失败，降级查余额: %s", e)

    # 2. 降级：查目标地址 USDT 余额
    try:
        addr_hex = "000000000000000000000000" + to_address[-40:]
        async with httpx.AsyncClient(timeout=5) as c2:
            r2 = await c2.post(
                f"{api_url}/wallet/triggersmartcontract",
                json={
                    "owner_address": to_address,
                    "contract_address": usdt_contract,
                    "function_selector": "balanceOf(address)",
                    "parameter": addr_hex,
                    "visible": True,
                },
                headers=headers,
            )
            if r2.status_code == 200:
                hex_val = r2.json().get("constant_result", ["0" * 64])[0]
                bal = int(hex_val, 16) if hex_val else 0
                estimated = 160_000 if bal == 0 else 75_000
                logger.info("降级余额判断: bal=%d, 估算=%d, to=%s", bal, estimated, to_address[:10])
                return estimated
    except Exception as e2:
        logger.warning("余额查询失败，使用默认 75000: %s", e2)

    return 75_000


# 模块级单例
tron_energy_service = TronEnergyService()
