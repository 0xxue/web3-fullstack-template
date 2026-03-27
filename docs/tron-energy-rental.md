# TRON 能量租赁机制

## 背景

TRON 上执行 TRC20（USDT）转账需要消耗两种资源：

| 资源 | 消耗量 | 不足时 |
|------|--------|--------|
| Bandwidth | ~345 pts | 消耗约 0.35 TRX |
| Energy | ~65,000–160,000 | 消耗约 27–67 TRX（链上烧毁） |

不提前租赁能量时，每笔 USDT 转账会额外烧掉 27–67 TRX。通过 feee.io 提前租赁，费用约 3–5 TRX，节省 80% 以上。

---

## 核心文件

| 文件 | 职责 |
|------|------|
| `backend/app/services/tron_energy.py` | 能量估算 + 租赁服务（公共层） |
| `backend/app/services/collection_executor.py` | 归集执行：每笔转账前估算+租赁 |
| `backend/app/services/payout_executor.py` | 批量打款执行：每笔转账前估算+租赁 |
| `backend/app/api/proposal.py` | 内部转账 / 多签打款提案：广播前估算+租赁 |

---

## 能量估算：`estimate_transfer_energy()`

位置：`tron_energy.py`

所有场景统一调用此函数估算单笔转账所需能量，替代各处原先的内联代码或固定值。

### 估算策略（两步）

```
Step 1: triggerconstantcontract 模拟
    向 TRON 节点模拟执行 transfer(to, amount)
    成功 → estimated = max(75_000, min(300_000, sim_result × 1.1))
    失败 → 进入 Step 2

Step 2: 降级——查目标地址 USDT 余额
    balanceOf(to_address)
    余额 = 0（全新地址，需新建存储槽）→ 160_000
    有余额（地址已存在槽）           → 75_000

兜底（Step 2 也失败）→ 75_000
```

### 为什么全新地址需要更多能量

TRON EVM 存储槽机制：首次向某地址转入 USDT，合约需要创建新的 storage slot，消耗额外约 25,000 energy。因此全新地址的实际消耗约 100,000–130,000，保守取 160,000。

### 函数签名

```python
async def estimate_transfer_energy(
    api_urls: list,       # TRON API 节点列表
    api_keys: list,       # API Key 列表（TronGrid）
    from_address: str,    # 发送地址
    to_address: str,      # 接收地址
    amount_sun: int,      # 转账金额（sun，1 USDT = 1_000_000 sun）
    usdt_contract: str,   # USDT 合约地址
) -> int:                 # 返回估算能量值
```

---

## 能量租赁：`TronEnergyService.ensure_energy()`

位置：`tron_energy.py`

估算完所需能量后，调用此方法检查地址当前已质押能量，不足时自动向 feee.io 租赁差额。

```
查询 getaccountresource → available = EnergyLimit - EnergyUsed
if available >= needed:
    直接返回（无需租赁）
else:
    shortage = needed - available
    调用 feee.io POST /open/v2/order/submit 租赁 shortage 个单位
```

### feee.io 配置（系统设置）

| 配置项 | 说明 |
|--------|------|
| `tron_energy_rental_enabled` | 是否启用能量租赁 |
| `tron_energy_rental_api_url` | feee.io API 地址（如 `https://feee.io`） |
| `tron_energy_rental_api_key` | API Key |
| `tron_energy_rental_max_price` | 每单位能量最高价（sun），默认 420 |
| `tron_energy_rental_duration` | 租赁时长（毫秒），默认 3,600,000（1小时） |

---

## 各场景调用流程

### 归集（collection_executor）

```
每笔 item（充值地址 → 归集钱包）：
  estimate_transfer_energy(from=充值地址, to=归集钱包, amount=item.amount)
  ensure_energy(address=充值地址, energy_needed=estimated)
  租赁失败 → 继续转账（烧 TRX）
  相邻两笔间隔 2s（feee.io 限频）
```

### 批量打款（payout_executor）

```
每笔 item（打款钱包 → 外部地址）：
  estimate_transfer_energy(from=打款钱包, to=item.to_address, amount=item.amount)
  ensure_energy(address=打款钱包, energy_needed=estimated)
  租赁失败 → 跳过此笔（标记 failed，避免烧大量 TRX）
  相邻两笔间隔 2s（feee.io 限频）
```

> 归集和打款的区别：打款租赁失败会跳过该笔（保守策略），归集则继续（宽松策略）。

### 内部转账提案（proposal._execute_tron_transfer_bg）

```
多签签名达到阈值后，后台执行：
  estimate_transfer_energy(from=多签钱包, to=目标钱包, amount=tx_amount)
  ensure_energy(address=多签钱包, energy_needed=estimated)
  等待能量到账（轮询 getaccountresource，最多 30s）
  补充 TRX 带宽费（< 2 TRX 时从 gas 钱包补）
  广播多签交易
```

### 多签打款提案（proposal._execute_tron_payout_bg）

```
多签签名达到阈值后，后台执行：
  estimate_transfer_energy(from=多签钱包, to=中转钱包, amount=total_amount)
  ensure_energy(address=多签钱包, energy_needed=estimated)
  补充 TRX 带宽费
  广播多签交易（多签钱包 → 中转钱包）
  等待链上确认（gettransactioninfobyid，最多 50s）
  触发 execute_payout()（中转钱包 → 各外部地址）
```

---

## 本次重构（统一能量估算）

### 改动前

| 场景 | 原来的方式 |
|------|-----------|
| 归集 | 固定 `USDT_TRANSFER_ENERGY = 80,000`，无模拟 |
| 批量打款 | `_estimate_tron_energy()`（内部函数，仅 Step 1，无余额降级） |
| 内部转账提案 | 内联代码（Step 1 + Step 2），最完整 |
| 多签打款提案 | 调 `_estimate_tron_energy()`（同批量打款，无余额降级） |

### 改动后

所有场景统一调用 `tron_energy.estimate_transfer_energy()`，包含完整的两步策略。

| 场景 | 改动 |
|------|------|
| `tron_energy.py` | 新增 `estimate_transfer_energy()` 公共函数 |
| `payout_executor.py` | 删除 `_estimate_tron_energy()`，改调公共函数 |
| `collection_executor.py` | 固定 80k → 改调公共函数（新增每笔估算） |
| `proposal.py` | 两处内联/私有估算代码 → 改调公共函数，删除重复逻辑 |

---

## 常见问题

**feee.io 返回 `code=20011: receive_address is invalid`**
接收地址未激活（从未收到过 TRX）。需先向该地址发送少量 TRX 激活后再租赁。

**feee.io 余额不足**
创建 TRON 多签打款时会预检 feee.io 余额，不足时拒绝创建（返回 400）。检查 feee.io 账户充值情况。

**能量租赁后等待不生效**
feee.io 委托到账有 3–6s 延迟，内部转账场景会轮询 `getaccountresource` 等待实际到账再广播。
