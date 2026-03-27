# TRON 多签打款完整流程

## 整体架构

```
多签钱包（打款钱包）
    ↓ ① 多签提案广播（_execute_tron_payout_bg）
中转钱包（relay wallet）
    ↓ ② 逐笔分发（payout_executor）
外部地址 A
外部地址 B
外部地址 C ...
```

TRON 多签打款分两个独立阶段，由不同代码负责：

| 阶段 | 代码位置 | 触发方式 |
|------|---------|---------|
| ① 多签广播（多签钱包 → 中转钱包） | `proposal.py / _execute_tron_payout_bg` | 提案签名达到阈值后自动触发 |
| ② 分发（中转钱包 → 外部地址） | `payout_executor.py / _do_execute` | 阶段①成功后 `asyncio.create_task` 触发 |

---

## 阶段一：多签广播

### 完整步骤

```
Step 1  补充多签钱包 TRX（带宽费保障）
Step 2  估算能量 + 租赁能量给多签钱包
Step 3  广播多签交易
Step 4  链上确认（gettransactioninfobyid）
Step 5  更新 DB + 触发阶段二
```

---

### Step 1：补充多签钱包 TRX

检查多签钱包 TRX 余额，不足 2 TRX 时从 gas 钱包补足。

| 情况 | 行为 |
|------|------|
| 多签钱包 TRX ≥ 2 | 跳过，直接进入 Step 2 |
| 多签钱包 TRX < 2 | 从 gas 钱包转入差额，等待 3s 到账 |
| 找不到 gas 钱包 | 跳过补充（继续广播，可能因带宽不足失败） |
| 补充失败（异常） | 记录 warning，继续广播（不阻断） |

> 2 TRX 是带宽兜底，实际每笔广播消耗约 0.3–0.5 TRX。

---

### Step 2：估算能量 + 租赁

**仅在 USDT 转账且启用了能量租赁时执行。**

```
estimate_transfer_energy(from=多签钱包, to=中转钱包, amount=总金额)
    → triggerconstantcontract 模拟
    → 成功：sim × 1.1，min 75k，max 300k
    → 失败：查中转钱包 USDT 余额
            = 0（全新地址）→ 160k
            > 0            → 75k

ensure_energy(address=多签钱包, energy_needed=estimated)
    → 查多签钱包已质押能量
    → 若足够：无需租赁
    → 若不足：向 feee.io 租赁差额
```

| 情况 | 行为 |
|------|------|
| 能量充足（已质押）| 无需租赁，直接进入 Step 3 |
| 租赁成功 | 等待 3s 让委托生效，进入 Step 3 |
| 租赁失败（feee.io 余额不足等）| 记录 warning，**继续广播**（将烧 TRX 支付 energy） |
| 租赁异常 | 同上，继续广播 |
| 未启用能量租赁 | 跳过，直接广播（烧 TRX） |

> 阶段一的能量租赁**不阻断**广播，原因：多签交易已经由所有签名方签好，不能因为能量问题丢弃。最坏情况是多烧一些 TRX。

---

### Step 3：广播多签交易

调用 `proposal_service.execute_tron_multisig_tx(tx_data, signatures)` 向 TRON 节点广播。

`broadcasttransaction` 返回 `result: true` + txID **不代表链上成功**，只代表交易进入广播池。

---

### Step 4：链上确认

等待 5s 出块后，轮询 `gettransactioninfobyid`，最多等 50s（10次 × 5s）。

| `receipt.result` | 含义 | 行为 |
|-----------------|------|------|
| `SUCCESS` | 链上执行成功 | `_confirmed = True`，进入 Step 5 |
| `FAILED` / `REVERT` / `OUT_OF_ENERGY` / `OUT_OF_TIME` | 链上执行失败 | 抛出 RuntimeError，`execution_error` 记录原因 |
| 空字符串（tx 已找到但结果未出）| 等待中 | break 退出循环，`_confirmed` 保持 False，**仍继续** |
| 50s 后仍未确认 | 查询超时 | 记录 warning，**仍继续**（交给阶段二兜底） |

> 注：当 `_confirmed=False`（未能确认）时不阻断，因为 `payout_executor` 在阶段二会等待中转钱包余额，若余额始终为 0 则自动终止。

---

### Step 5：更新 DB + 触发阶段二

| 结果 | proposal 状态 | payout 状态 | 下一步 |
|------|-------------|------------|--------|
| 广播+链上均成功 | `executed` | `executing`（wallet_id 切换为中转钱包） | 触发 `execute_payout()` |
| 广播失败或链上 REVERT | `rejected` | `failed` | 终止，需重新发起提案 |

---

## 阶段二：分发（payout_executor）

### 完整步骤

```
Step 1  加载批次 + 明细 + 中转钱包私钥
Step 2  等待中转钱包余额到账（最多 90s）
Step 3  检查中转钱包 TRX，不足从 gas 钱包补
Step 4  逐笔估算能量 + 租赁
Step 5  串行转账
Step 6  更新批次状态 + TG 通知
```

---

### Step 2：等待资金到账

轮询中转钱包余额（每 3s，最多 30 次 = 90s），直到余额 ≥ 所有 pending item 总额。

| 情况 | 行为 |
|------|------|
| 余额足够（90s 内到账）| 继续 Step 3 |
| 90s 超时余额仍不足 | payout.status = `failed`，所有 pending items → `failed`，**终止** |

> 超时通常意味着阶段一的多签交易链上 REVERT（资金实际上没转出来）。

**特殊情况——中转钱包已有余额：**

若中转钱包在提案执行前已有其他 USDT（如上次打款剩余），会被计入余额，可能提前满足条件直接通过。这不是 bug，但要注意金额合理性。

---

### Step 3：检查并补充 TRX

根据打款类型计算中转钱包所需 TRX：

```
USDT 打款：trx_needed = GAS_ESTIMATE_TRON × GAS_BUFFER_MULTIPLIER × 笔数
TRX 打款： trx_needed = 发出总量 + GAS_ESTIMATE_TRON × GAS_BUFFER_MULTIPLIER × 笔数
```

| 情况 | 行为 |
|------|------|
| 中转钱包 TRX ≥ trx_needed | 跳过 |
| 中转钱包 TRX < trx_needed | 从 gas 钱包补入差额，等待 3s |
| 找不到 gas 钱包 | 跳过补充（记录 warning） |
| 补充失败 | 记录 warning，**不阻断**（继续打款） |

---

### Step 4：逐笔估算能量 + 租赁（仅 TRON USDT）

对每一笔 pending item 分别处理：

```
estimate_transfer_energy(from=中转钱包, to=外部地址, amount=item.amount)
ensure_energy(address=中转钱包, energy_needed=estimated)
```

| 情况 | 行为 |
|------|------|
| 能量充足（已质押）| 放行（加入 `energy_ok_ids`） |
| 租赁成功 | 放行（加入 `energy_ok_ids`） |
| 租赁失败 | **不放行**，该笔标 `failed`（"能量租赁失败，跳过转账以避免 TRX 浪费"） |
| 未启用租赁 | 全部放行（依赖已质押能量或烧 TRX） |
| 整体异常 | 保守处理：全部不放行，全部标 `failed` |

每笔租赁间隔 2s（feee.io 限频保护）。

> 与归集不同：打款租赁失败**跳过该笔**（保守策略），因为外部打款金额可能较大，不值得烧 TRX。

---

### Step 5：串行转账

每笔转账使用中转钱包私钥串行发送（同一私钥不能并发，否则 sequence 冲突）。

| 情况 | 行为 |
|------|------|
| 能量不在 `energy_ok_ids` | 跳过，item → `failed` |
| 转账成功 | item.tx_hash = txHash，item → `completed` |
| 转账异常（网络错误、余额不足等）| item → `failed`，error_message 记录原因，retry_count +1 |

---

### Step 6：最终状态

| 结果 | payout 状态 |
|------|------------|
| 全部成功 | `completed` |
| 全部失败 | `failed` |
| 部分成功 | `partial` |

---

## 异常场景总结

### 场景 A：多签钱包没有 TRX

```
Step 1 补充：从 gas 钱包补到 2 TRX ✓
Step 2 能量：租赁成功，等待 3s ✓
Step 3 广播：正常广播 ✓
链上执行：USDT transfer 消耗 energy（已租赁），带宽消耗 TRX（2 TRX 够用） ✓
```

如果 gas 钱包也没有 TRX：补充失败 → 广播时可能因 BANDWIDTH_ERROR 失败 → 链上 REVERT → 阶段二超时 → 全部 failed。

---

### 场景 B：feee.io 没有 TRX（能量租赁失败）

```
Step 2 能量：租赁失败，记录 warning，继续广播
Step 3 广播：广播成功（txID 返回）
链上执行：多签钱包无能量 → 消耗 TRX 烧毁支付 energy（约 27–67 TRX）
    多签钱包 TRX 足够 → 链上 SUCCESS → 阶段二正常执行
    多签钱包 TRX 不够 → 链上 OUT_OF_ENERGY / REVERT → execution_error → payout failed
```

---

### 场景 C：中转钱包已有 USDT（上次遗留）

```
阶段二 Step 2：余额查询 = 遗留 + 新到账
若总余额 ≥ 本次所需 → 直接通过（不会等待新资金）
```

实际上会把遗留余额也打出去，只要总量够。这是合理行为，运营需自行管理余额。

---

### 场景 D：中转钱包 USDT 够但 TRX 不足

```
阶段二 Step 3：gas 钱包补充 TRX 差额 ✓
（能量在 Step 4 通过 feee.io 租赁解决，不依赖中转钱包 TRX 支付能量）
```

---

### 场景 E：外部地址是全新地址（从未收到过 USDT）

```
阶段二 Step 4 能量估算：
    triggerconstantcontract 模拟 → 可能返回 ~100k（含建槽费）
    × 1.1 → 约 110k
    租赁 110k energy 给中转钱包
链上执行：建槽 + transfer 消耗约 100–130k energy ✓
```

若模拟失败降级：余额 = 0 → 估算 160k（保守上限），足够覆盖建槽。

---

### 场景 F：部分外部地址租赁失败

```
租赁成功的 item → energy_ok_ids → 正常转账
租赁失败的 item → 标 failed（"能量租赁失败，跳过转账以避免 TRX 浪费"）
最终 payout 状态：partial（部分成功）
```

需手动重新对失败的地址发起新的打款。

---

## 文件对应关系

| 步骤 | 文件 | 函数 |
|------|------|------|
| 提案签名达到阈值 | `api/proposal.py` | `sign_proposal()` |
| 多签广播（阶段一）| `api/proposal.py` | `_execute_tron_payout_bg()` |
| 分发执行（阶段二）| `services/payout_executor.py` | `execute_payout()` / `_do_execute()` |
| 能量估算 | `services/tron_energy.py` | `estimate_transfer_energy()` |
| 能量租赁 | `services/tron_energy.py` | `TronEnergyService.ensure_energy()` |
| 链上转账 | `services/chain_client.py` | `send_usdt()` / `send_native()` |
