import { createFileRoute, useNavigate, Link } from '@tanstack/react-router'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Modal } from '@/components/ui/modal'
import { ArrowLeftRight, Send, Clock, Loader2, CheckCircle2, ExternalLink, AlertTriangle, Plus, Trash2, Upload, RefreshCw } from 'lucide-react'
import { Select } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { toast } from 'sonner'
import { proposalApi, settingsApi, payoutApi, transferApi } from '@/lib/api'
import type { MultisigProposal } from '@/types'

type WalletInfo = {
    id: number
    chain: string
    type: string
    address: string | null
    label: string | null
    is_multisig: boolean
    usdt_balance: string | null
    native_balance: string | null
    multisig_status: string | null
    relay_wallet_id: number | null
    is_relay_wallet: boolean
}

// Gas 预留量：下一次转账需要的最低 gas
const GAS_RESERVE: Record<string, number> = {
    BSC: 0.005,   // ~0.003 BNB per Safe execTransaction
    TRON: 3,      // ~2 TRX per transfer (with energy rental)
}

export const Route = createFileRoute('/payouts')({
    validateSearch: (search: Record<string, unknown>) => ({
        tab: (search.tab as string) || 'internal',
    }),
    component: PayoutsComponent,
})

function getTokenLabel(chain: string, token: string) {
    if (token === 'usdt') return 'USDT'
    return chain === 'BSC' ? 'BNB' : 'TRX'
}

function getTxMemo(tx_data: { _memo?: string; [key: string]: unknown } | null): string | null {
    return tx_data?._memo ?? null
}

function getTokenOptions(chain: string) {
    return [
        { value: 'usdt', label: 'USDT' },
        { value: 'native', label: chain === 'BSC' ? 'BNB' : 'TRX' },
    ]
}

function formatBalance(bal: string | null | undefined) {
    if (!bal) return '0.00'
    const n = parseFloat(bal)
    return isNaN(n) ? '0.00' : n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
}

function PayoutsComponent() {
    const navigate = useNavigate()
    const { tab: initialTab } = Route.useSearch()
    const [activeTab, setActiveTab] = useState(initialTab || 'internal')
    const [confirmModalOpen, setConfirmModalOpen] = useState(false)
    const [formType, setFormType] = useState<'internal' | 'external'>('internal')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [submitSuccess, setSubmitSuccess] = useState(false)

    // Wallet data
    const [wallets, setWallets] = useState<WalletInfo[]>([])
    const [walletsLoading, setWalletsLoading] = useState(true)

    // Internal transfer form state
    const [internalChain, setInternalChain] = useState('BSC')
    const [internalToken, setInternalToken] = useState('usdt')
    const [internalSourceId, setInternalSourceId] = useState('') // 来源钱包 ID
    const [internalAmount, setInternalAmount] = useState('')
    const [internalError, setInternalError] = useState('')

    // External payout form state (single-address, kept for internal logic compatibility)
    const [externalChain] = useState('BSC')
    const [externalToken, setExternalToken] = useState('usdt')
    const [externalSourceId, setExternalSourceId] = useState('')
    const [externalAddress, setExternalAddress] = useState('')
    const [externalAmount, setExternalAmount] = useState('')
    const [externalMemo, setExternalMemo] = useState('')
    const [, setExternalError] = useState('')

    // Batch payout state
    type BatchItem = { id: number; address: string; amount: string; memo: string }
    type PrecheckResult = {
        ok: boolean
        total_amount: number
        usdt_balance: number
        usdt_sufficient: boolean
        estimated_gas_native: number
        native_balance: number
        native_sufficient: boolean
        gas_auto_supplement: boolean
        estimated_energy_cost_trx: number | null
        feee_balance_trx: number | null
        feee_balance_sufficient: boolean | null
        estimated_feee_cost_trx: number | null
    }
    const [batchChain, setBatchChain] = useState('BSC')
    const [batchAssetType, setBatchAssetType] = useState('usdt')
    const [batchWalletId, setBatchWalletId] = useState('')
    const [batchItems, setBatchItems] = useState<BatchItem[]>([{ id: 1, address: '', amount: '', memo: '' }])
    const [batchMemo, setBatchMemo] = useState('')
    const [batchError, setBatchError] = useState('')
    const [batchSubmitting, setBatchSubmitting] = useState(false)
    const [batchItemCounter, setBatchItemCounter] = useState(2)
    const [batchPrecheckResult, setBatchPrecheckResult] = useState<PrecheckResult | null>(null)
    const [batchPrecheckOpen, setBatchPrecheckOpen] = useState(false)
    const [batchPrecheckLoading, setBatchPrecheckLoading] = useState(false)
    const [batchSubmitItems, setBatchSubmitItems] = useState<{ to_address: string; amount: string; memo?: string }[]>([])

    // History
    type DirectTransferRecord = {
        id: number; created_at: string; admin_username: string
        chain: string; token: string; token_label: string
        from_address: string; to_address: string; amount: string
        tx_hash: string; memo: string; wallet_label: string
    }
    type PayoutBatch = {
        id: number; chain: string; asset_type: string; status: string
        total_amount: string; item_count: number; memo: string | null
        proposal_id: number | null; created_by_username: string | null
        created_at: string; executed_at: string | null
    }
    const [history, setHistory] = useState<MultisigProposal[]>([])
    const [directTransfers, setDirectTransfers] = useState<DirectTransferRecord[]>([])
    const [payoutBatches, setPayoutBatches] = useState<PayoutBatch[]>([])
    const [historyLoading, setHistoryLoading] = useState(false)
    const [, setHistoryTotal] = useState(0)
    const [historySubTab, setHistorySubTab] = useState<'batches' | 'transfers'>('batches')
    const [batchPage, setBatchPage] = useState(1)
    const [batchPageSize, setBatchPageSize] = useState(10)
    const [transferPage, setTransferPage] = useState(1)
    const [transferPageSize, setTransferPageSize] = useState(10)

    // Detail modal
    const [detailOpen, setDetailOpen] = useState(false)
    const [detailProposal, setDetailProposal] = useState<MultisigProposal | null>(null)
    const [detailLoading, setDetailLoading] = useState(false)

    // 切换链时重置
    useEffect(() => { setInternalToken('usdt'); setInternalSourceId('') }, [internalChain])
    useEffect(() => { setExternalToken('usdt'); setExternalSourceId('') }, [externalChain])

    // ─── 加载钱包数据 ───────────────────────────────
    const fetchWallets = useCallback(async () => {
        try {
            // 第一步：加载钱包列表（无余额），立即渲染表单
            setWalletsLoading(true)
            const { data } = await settingsApi.getWallets()
            setWallets(data)
            setWalletsLoading(false)
            // 第二步：后台异步加载余额，填充后更新（不阻塞表单显示）
            settingsApi.getWalletsWithBalances()
                .then(res => setWallets(res.data))
                .catch(() => {/* 余额加载失败不影响操作 */})
        } catch {
            toast.error('加载钱包数据失败')
            setWalletsLoading(false)
        }
    }, [])

    useEffect(() => { fetchWallets() }, [fetchWallets])

    // ─── 加载历史记录 ───────────────────────────────
    const fetchHistory = useCallback(async () => {
        try {
            setHistoryLoading(true)
            const [transferRes, payoutRes, directRes, batchRes] = await Promise.all([
                proposalApi.list({ page: 1, page_size: 50, type: 'transfer' }),
                proposalApi.list({ page: 1, page_size: 50, type: 'payout' }),
                transferApi.list({ page_size: 50 }),
                payoutApi.list({ page: 1, page_size: 50 }),
            ])
            const all = [
                ...(transferRes.data.items || []),
                ...(payoutRes.data.items || []),
            ].sort((a: MultisigProposal, b: MultisigProposal) =>
                new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
            )
            setHistory(all)
            setDirectTransfers(directRes.data.items || [])
            setPayoutBatches(batchRes.data.items || [])
            setHistoryTotal(all.length + (directRes.data.items?.length || 0))
        } catch {
            // silent
        } finally {
            setHistoryLoading(false)
        }
    }, [])

    useEffect(() => {
        if (activeTab === 'history') fetchHistory()
    }, [activeTab, fetchHistory])

    // ─── 查看详情 ────────────────────────────────────
    const handleViewDetail = async (proposal: MultisigProposal) => {
        setDetailOpen(true)
        setDetailLoading(true)
        try {
            const { data } = await proposalApi.getDetail(proposal.id)
            setDetailProposal(data)
        } catch {
            setDetailProposal(proposal)
        } finally {
            setDetailLoading(false)
        }
    }

    // 钱包类型中文标签
    const WALLET_TYPE_LABEL: Record<string, string> = {
        collection: '归集钱包',
        payout: '打款钱包',
        gas: 'Gas钱包',
        safe: '多签钱包',
    }
    const getTypeLabel = (w: WalletInfo) => WALLET_TYPE_LABEL[w.type] || w.type

    // ─── 获取链上可用于内部转账的钱包（多签 + gas 钱包）──────────────────
    const getInternalTransferWallets = (chain: string) =>
        wallets.filter(w =>
            w.chain === chain &&
            w.address &&
            ((w.is_multisig && w.multisig_status === 'active') || w.type === 'gas')
        )

    const getActiveMultisigWallets = (chain: string) =>
        wallets.filter(w => w.chain === chain && w.is_multisig && w.multisig_status === 'active')

    const internalWallets = useMemo(() => getInternalTransferWallets(internalChain), [wallets, internalChain])
    const externalWallets = useMemo(() => getActiveMultisigWallets(externalChain), [wallets, externalChain])

    // 自动选第一个
    useEffect(() => {
        if (internalWallets.length > 0 && !internalSourceId) {
            setInternalSourceId(String(internalWallets[0].id))
        }
    }, [internalWallets, internalSourceId])

    useEffect(() => {
        if (externalWallets.length > 0 && !externalSourceId) {
            setExternalSourceId(String(externalWallets[0].id))
        }
    }, [externalWallets, externalSourceId])

    // 批量打款专用钱包（type=payout）：
    //   - 普通钱包（非多签）：系统直接持有私钥，直接执行
    //   - BSC 多签：Safe MultiSend 一次执行
    //   - TRON 多签：需有中转钱包（relay_wallet_id），通过中转钱包分发
    const batchPayoutWallets = useMemo(() =>
        wallets.filter(w => {
            if (w.chain !== batchChain || w.type !== 'payout') return false
            if (w.is_relay_wallet) return false  // 排除中转钱包（永久标记，不依赖引用关系）
            if (!w.is_multisig) return true  // 普通打款钱包直接可用
            if (w.chain === 'BSC') return true  // BSC 多签：MultiSend
            if (w.chain === 'TRON') return !!w.relay_wallet_id  // TRON 多签：需有中转钱包
            return false
        }),
        [wallets, batchChain])

    useEffect(() => {
        if (batchPayoutWallets.length > 0) {
            setBatchWalletId(String(batchPayoutWallets[0].id))
        } else {
            setBatchWalletId('')
        }
    }, [batchPayoutWallets])

    const internalSource = wallets.find(w => w.id === Number(internalSourceId))
    const externalSource = wallets.find(w => w.id === Number(externalSourceId))

    const getWalletBalance = (wallet: WalletInfo | undefined, token: string) => {
        if (!wallet) return '0'
        return token === 'native' ? (wallet.native_balance || '0') : (wallet.usdt_balance || '0')
    }

    const getMaxNativeAmount = (wallet: WalletInfo | undefined, chain: string) => {
        if (!wallet) return 0
        const bal = parseFloat(wallet.native_balance || '0')
        const reserve = GAS_RESERVE[chain] || 0
        return Math.max(0, bal - reserve)
    }

    // 钱包选项（下拉）—— 显示 [类型标签] 名称 — 地址，sublabel 显示余额
    const walletSelectOptions = (list: WalletInfo[], token?: string) =>
        list.map(w => {
            const usdtBal = w.usdt_balance ? parseFloat(w.usdt_balance) : null
            const nativeBal = w.native_balance ? parseFloat(w.native_balance) : null
            const nativeLabel = w.chain === 'BSC' ? 'BNB' : 'TRX'
            let sublabel = ''
            if (token === 'native') {
                sublabel = nativeBal !== null ? `${nativeBal.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 6 })} ${nativeLabel}` : '余额未知'
            } else {
                const parts: string[] = []
                if (usdtBal !== null) parts.push(`USDT ${usdtBal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
                if (nativeBal !== null) parts.push(`${nativeBal.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })} ${nativeLabel}`)
                sublabel = parts.length ? parts.join('  ·  ') : '余额未知'
            }
            return {
                value: String(w.id),
                label: `[${getTypeLabel(w)}] ${w.label || ''} — ${w.address ? w.address.slice(0, 8) + '...' + w.address.slice(-6) : '无地址'}`.trim(),
                sublabel,
            }
        })

    // 内部转账：目标为系统内所有活跃多签钱包 + gas钱包（排除来源钱包自身）
    const internalTargetOptions = useMemo(() => {
        const nativeLabel = internalChain === 'BSC' ? 'BNB' : 'TRX'
        return wallets
            .filter(w =>
                w.chain === internalChain &&
                w.address &&
                String(w.id) !== internalSourceId &&
                ((w.is_multisig && w.multisig_status === 'active') || w.type === 'gas')
            )
            .map(w => {
                const usdtBal = w.usdt_balance ? parseFloat(w.usdt_balance) : null
                const nativeBal = w.native_balance ? parseFloat(w.native_balance) : null
                let sublabel = ''
                if (internalToken === 'native') {
                    sublabel = nativeBal !== null ? `${nativeBal.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 6 })} ${nativeLabel}` : '余额未知'
                } else {
                    const parts: string[] = []
                    if (usdtBal !== null) parts.push(`USDT ${usdtBal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
                    if (nativeBal !== null) parts.push(`${nativeBal.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })} ${nativeLabel}`)
                    sublabel = parts.length ? parts.join('  ·  ') : '余额未知'
                }
                return {
                    value: w.address || '',
                    label: `[${getTypeLabel(w)}] ${w.label || ''} — ${w.address ? w.address.slice(0, 8) + '...' + w.address.slice(-6) : ''}`.trim(),
                    sublabel,
                }
            })
    }, [wallets, internalChain, internalSourceId, internalToken])

    const [internalTargetSelect, setInternalTargetSelect] = useState('')

    useEffect(() => {
        if (internalTargetOptions.length > 0) {
            setInternalTargetSelect(internalTargetOptions[0].value)
        } else {
            setInternalTargetSelect('')
        }
    }, [internalTargetOptions])

    const resolvedInternalTarget = internalTargetSelect

    // ─── 表单验证 + 确认弹窗 ────────────────────────
    const triggerConfirm = (type: 'internal' | 'external') => {
        if (type === 'internal') {
            if (!internalSource) { toast.error('请选择来源钱包'); return }

            const target = resolvedInternalTarget
            if (!target?.trim()) { setInternalError('请选择或输入目标地址'); return }

            const amt = parseFloat(internalAmount)
            if (!internalAmount || isNaN(amt) || amt <= 0) {
                setInternalError('请输入有效的转账金额'); return
            }

            if (internalToken === 'native') {
                const maxAmt = getMaxNativeAmount(internalSource, internalChain)
                if (amt > maxAmt) {
                    const nativeLabel = getTokenLabel(internalChain, 'native')
                    setInternalError(`最大可转 ${maxAmt.toFixed(6)} ${nativeLabel}（需预留 ${GAS_RESERVE[internalChain]} ${nativeLabel} 作为 Gas）`)
                    return
                }
            }

            setInternalError('')
        }

        if (type === 'external') {
            if (!externalSource) { toast.error('请选择来源钱包'); return }
            if (!externalAddress.trim()) { setExternalError('请输入客户地址'); return }

            const amt = parseFloat(externalAmount)
            if (!externalAmount || isNaN(amt) || amt <= 0) {
                setExternalError('请输入有效的出款金额'); return
            }

            if (externalToken === 'native') {
                const maxAmt = getMaxNativeAmount(externalSource, externalChain)
                if (amt > maxAmt) {
                    const nativeLabel = getTokenLabel(externalChain, 'native')
                    setExternalError(`最大可转 ${maxAmt.toFixed(6)} ${nativeLabel}（需预留 ${GAS_RESERVE[externalChain]} ${nativeLabel} 作为 Gas）`)
                    return
                }
            }

            setExternalError('')
        }

        setFormType(type)
        setConfirmModalOpen(true)
    }

    const getConfirmData = () => {
        if (formType === 'internal') {
            return {
                type: '内部转账',
                chain: internalChain,
                token: internalToken,
                tokenLabel: getTokenLabel(internalChain, internalToken),
                amount: internalAmount,
                address: resolvedInternalTarget || '',
                fromWallet: internalSource,
            }
        }
        return {
            type: '外部出款',
            chain: externalChain,
            token: externalToken,
            tokenLabel: getTokenLabel(externalChain, externalToken),
            amount: externalAmount,
            address: externalAddress,
            fromWallet: externalSource,
        }
    }

    // ─── 提交提案 / 直接转账 ─────────────────────────
    const handleConfirmSubmit = async () => {
        setIsSubmitting(true)
        const data = getConfirmData()
        const isGasWallet = data.fromWallet?.type === 'gas'

        try {
            if (formType === 'internal' && isGasWallet) {
                // Gas 钱包：直接广播，不走多签提案
                await transferApi.direct({
                    chain: data.chain,
                    wallet_id: data.fromWallet!.id,
                    to_address: data.address,
                    amount: data.amount,
                    token: data.token,
                })

                setIsSubmitting(false)
                setSubmitSuccess(true)
                toast.success('转账已广播', {
                    description: `${data.amount} ${data.tokenLabel} → ${data.address.slice(0, 10)}...`,
                })
                setTimeout(() => {
                    setSubmitSuccess(false)
                    setConfirmModalOpen(false)
                    setInternalAmount('')
                    fetchWallets() // 刷新余额
                }, 1200)

            } else {
                // 多签钱包：创建提案，等待签名
                const proposalType = formType === 'internal' ? 'transfer' : 'payout'
                const walletId = data.fromWallet!.id
                const title = formType === 'internal'
                    ? `内部转账 ${data.amount} ${data.tokenLabel} (${data.chain})`
                    : `出款 ${data.amount} ${data.tokenLabel} → ${data.address.slice(0, 10)}...`

                await proposalApi.create({
                    chain: data.chain,
                    type: proposalType,
                    wallet_id: walletId,
                    title,
                    to_address: data.address,
                    amount: data.amount,
                    token: data.token,
                    memo: formType === 'external' ? externalMemo || undefined : undefined,
                })

                setIsSubmitting(false)
                setSubmitSuccess(true)
                toast.success('提案已创建，等待签名', {
                    description: `${data.type} ${data.amount} ${data.tokenLabel}`,
                })
                setTimeout(() => {
                    setSubmitSuccess(false)
                    setConfirmModalOpen(false)
                    if (formType === 'internal') {
                        setInternalAmount('')
                    } else {
                        setExternalAddress('')
                        setExternalAmount('')
                        setExternalMemo('')
                    }
                    navigate({ to: '/multisig' })
                }, 1200)
            }

        } catch (e: unknown) {
            setIsSubmitting(false)
            const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败'
            toast.error(msg)
        }
    }

    // ─── 余额刷新 ───────────────────────────────────
    const [balanceRefreshing, setBalanceRefreshing] = useState(false)

    const handleRefreshBalance = async () => {
        setBalanceRefreshing(true)
        try {
            const { data } = await settingsApi.getWalletsWithBalances()
            setWallets(data)
            toast.success('余额已刷新')
        } catch {
            toast.error('刷新余额失败')
        } finally {
            setBalanceRefreshing(false)
        }
    }

    // ─── 余额卡片 ───────────────────────────────────
    const WalletCard = ({ wallet, label, chain, token }: {
        wallet: WalletInfo | undefined
        label: string
        chain: string
        token: string
    }) => {
        const tokenLabel = getTokenLabel(chain, token)
        const balance = getWalletBalance(wallet, token)
        const reserve = token === 'native' ? GAS_RESERVE[chain] : 0

        return (
            <div className="bg-gray-50 dark:bg-[#22252e] p-4 rounded-xl border border-gray-100 dark:border-[#2a2d35]">
                <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">{label}</div>
                <div className="font-bold text-lg dark:text-white text-zinc-900">
                    {wallet
                        ? <>{formatBalance(balance)} <span className="text-sm text-gray-400 font-normal">{tokenLabel}</span></>
                        : <span className="text-sm text-red-400">未选择</span>
                    }
                </div>
                {wallet?.address && (
                    <div className="text-[10px] text-gray-400 font-mono mt-1 truncate">{wallet.address}</div>
                )}
                {token === 'native' && wallet && reserve > 0 && (
                    <div className="text-[10px] text-amber-500 mt-1 flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3" />
                        预留 {reserve} {tokenLabel} Gas
                    </div>
                )}
            </div>
        )
    }

    // ─── 预检行组件 ─────────────────────────────────
    const CheckRow = ({ label, needed, actual, ok }: { label: string; needed: string; actual: string; ok: boolean }) => (
        <div className="flex items-center justify-between bg-gray-50 dark:bg-[#22252e] rounded-xl p-3 gap-4">
            <div className="flex flex-col gap-0.5 min-w-0">
                <span className="text-xs text-gray-500 dark:text-gray-400">{label}</span>
                <span className="text-sm font-semibold text-zinc-900 dark:text-white">余额 {actual}</span>
                <span className="text-xs text-gray-400">需要 {needed}</span>
            </div>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${ok ? 'bg-emerald-100 dark:bg-emerald-500/20' : 'bg-red-100 dark:bg-red-500/20'}`}>
                {ok
                    ? <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                    : <AlertTriangle className="w-4 h-4 text-red-500" />
                }
            </div>
        </div>
    )

    const confirmData = getConfirmData()

    // 找到选中的目标钱包
    const internalTargetWallet = internalTargetSelect
        ? wallets.find(w => w.address === internalTargetSelect)
        : undefined

    // 预计算提案备注（避免 tx_data unknown 类型直接进入 JSX）
    const proposalMemo: string | null = getTxMemo(detailProposal?.tx_data ?? null)

    return (
        <div className="w-full flex flex-col gap-6">
            <div>
                <h1 className="text-2xl font-bold text-zinc-900 dark:text-white mb-1">出款管理</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">发起安全转账至外部地址或内部储备。</p>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="internal">内部转账</TabsTrigger>
                    <TabsTrigger value="external">外部出款</TabsTrigger>
                    <TabsTrigger value="history">历史记录</TabsTrigger>
                </TabsList>

                {/* ─── 内部转账 ──────────────────────────── */}
                <TabsContent value="internal">
                    <div className="max-w-2xl mt-4">
                        <div className="elegant-card p-8 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35] flex flex-col gap-6">
                            <div className="flex items-center gap-3 text-blue-600 dark:text-blue-400 mb-2">
                                <div className="w-10 h-10 rounded-xl bg-blue-50 dark:bg-blue-500/10 flex items-center justify-center">
                                    <ArrowLeftRight className="w-5 h-5" />
                                </div>
                                <div>
                                    <h3 className="font-semibold">在多签钱包之间转移资金</h3>
                                    <p className="text-xs text-gray-500 dark:text-gray-400">选择来源和目标钱包，需要多签审批</p>
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">网络</label>
                                    <Select
                                        value={internalChain}
                                        onChange={setInternalChain}
                                        options={[
                                            { value: 'BSC', label: 'BSC (BNB Smart Chain)' },
                                            { value: 'TRON', label: 'TRON' },
                                        ]}
                                    />
                                </div>
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">代币</label>
                                    <Select
                                        value={internalToken}
                                        onChange={setInternalToken}
                                        options={getTokenOptions(internalChain)}
                                    />
                                </div>
                            </div>

                            {walletsLoading ? (
                                <div className="flex items-center justify-center py-8 text-gray-400">
                                    <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载钱包数据...
                                </div>
                            ) : internalWallets.length === 0 ? (
                                <div className="text-center py-6 text-red-400 text-sm">
                                    未找到 {internalChain} 链上的活跃多签钱包
                                </div>
                            ) : (
                                <>
                                    {/* 来源钱包 */}
                                    <div>
                                        <div className="flex items-center justify-between mb-2">
                                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300">来源钱包</label>
                                            <button
                                                type="button"
                                                onClick={handleRefreshBalance}
                                                disabled={balanceRefreshing}
                                                className="flex items-center gap-1 text-xs text-gray-400 hover:text-blue-500 transition-colors disabled:opacity-50"
                                            >
                                                <RefreshCw className={`w-3 h-3 ${balanceRefreshing ? 'animate-spin' : ''}`} />
                                                刷新余额
                                            </button>
                                        </div>
                                        <Select
                                            value={internalSourceId}
                                            onChange={setInternalSourceId}
                                            options={walletSelectOptions(internalWallets, internalToken)}
                                        />
                                    </div>

                                    {/* 目标 */}
                                    <div>
                                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">目标钱包</label>
                                        {internalTargetOptions.length === 0 ? (
                                            <div className="text-sm text-amber-500 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 rounded-lg p-3">
                                                当前链上暂无其他可用的系统钱包作为转账目标
                                            </div>
                                        ) : (
                                            <Select
                                                value={internalTargetSelect}
                                                onChange={setInternalTargetSelect}
                                                options={internalTargetOptions}
                                            />
                                        )}
                                    </div>

                                    {/* 余额展示 */}
                                    <div className="flex flex-col sm:grid sm:grid-cols-[1fr_auto_1fr] items-center gap-3">
                                        <WalletCard wallet={internalSource} label="来源" chain={internalChain} token={internalToken} />
                                        <ArrowLeftRight className="w-5 h-5 text-gray-400 rotate-90 sm:rotate-0" />
                                        <WalletCard
                                            wallet={internalTargetWallet}
                                            label="目标"
                                            chain={internalChain}
                                            token={internalToken}
                                        />
                                    </div>

                                    {/* 金额 */}
                                    <div>
                                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">
                                            转账金额 ({getTokenLabel(internalChain, internalToken)})
                                        </label>
                                        <Input
                                            type="number"
                                            placeholder="输入金额"
                                            className={`h-11 text-lg ${internalError ? 'border-red-300 dark:border-red-700' : ''}`}
                                            value={internalAmount}
                                            onChange={(e) => { setInternalAmount(e.target.value); setInternalError('') }}
                                        />
                                        {internalToken === 'native' && internalSource && (
                                            <p className="text-xs text-gray-500 mt-1.5">
                                                可用: {getMaxNativeAmount(internalSource, internalChain).toFixed(6)} {getTokenLabel(internalChain, internalToken)}
                                                <button
                                                    className="text-blue-500 ml-2 hover:underline"
                                                    onClick={() => setInternalAmount(String(getMaxNativeAmount(internalSource, internalChain)))}
                                                >
                                                    全部
                                                </button>
                                            </p>
                                        )}
                                        {internalError && <p className="text-xs text-red-500 mt-1.5">{internalError}</p>}
                                    </div>

                                    <Button
                                        variant="primary"
                                        size="lg"
                                        className="w-full mt-2"
                                        onClick={() => triggerConfirm('internal')}
                                    >
                                        提案：内部转账
                                    </Button>
                                </>
                            )}
                        </div>
                    </div>
                </TabsContent>

                {/* ─── 外部批量出款 ──────────────────────── */}
                <TabsContent value="external">
                    <div className="max-w-4xl mt-4 flex flex-col gap-4">
                        {/* 头部 */}
                        <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                            <div className="flex items-center gap-3 text-amber-600 dark:text-amber-400 mb-4">
                                <div className="w-10 h-10 rounded-xl bg-amber-50 dark:bg-amber-500/10 flex items-center justify-center">
                                    <Send className="w-5 h-5" />
                                </div>
                                <div>
                                    <h3 className="font-semibold">批量打款到外部地址</h3>
                                    <p className="text-xs text-gray-500 dark:text-gray-400">从打款钱包批量转 USDT 到多个外部地址，需多签审批后自动执行</p>
                                </div>
                            </div>

                            <div className="grid grid-cols-3 gap-4">
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">网络</label>
                                    <Select
                                        value={batchChain}
                                        onChange={(v) => { setBatchChain(v); setBatchWalletId('') }}
                                        options={[
                                            { value: 'BSC', label: 'BSC' },
                                            { value: 'TRON', label: 'TRON' },
                                        ]}
                                    />
                                </div>
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">代币</label>
                                    <Select
                                        value={batchAssetType}
                                        onChange={setBatchAssetType}
                                        options={getTokenOptions(batchChain)}
                                    />
                                </div>
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">打款钱包</label>
                                    {walletsLoading ? (
                                        <div className="h-11 flex items-center text-gray-400 text-sm"><Loader2 className="w-4 h-4 animate-spin mr-2" />加载中</div>
                                    ) : batchPayoutWallets.length === 0 ? (
                                        <div className="h-11 flex items-center text-red-400 text-sm">无可用打款钱包</div>
                                    ) : (
                                        <Select
                                            value={batchWalletId}
                                            onChange={setBatchWalletId}
                                            options={batchPayoutWallets.map(w => ({
                                                value: String(w.id),
                                                label: `${w.is_multisig ? '[多签] ' : ''}${w.label || ''} — ${w.address ? w.address.slice(0, 8) + '...' + w.address.slice(-6) : ''}${w.chain === 'TRON' && w.is_multisig ? ' [中转执行]' : ''}`.trim(),
                                            }))}
                                        />
                                    )}
                                    {(() => {
                                        const w = wallets.find(w => String(w.id) === batchWalletId)
                                        if (!w) return null
                                        return (
                                            <div className="mt-1 space-y-0.5">
                                                <p className="text-xs text-gray-500 flex items-center gap-1">
                                                    USDT: {formatBalance(w.usdt_balance)} &nbsp;|&nbsp; {batchChain === 'BSC' ? 'BNB' : 'TRX'}: {formatBalance(w.native_balance)}
                                                    <button
                                                        type="button"
                                                        onClick={fetchWallets}
                                                        className="ml-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                                                        title="刷新余额"
                                                    >
                                                        <RefreshCw className={`w-3 h-3 ${walletsLoading ? 'animate-spin' : ''}`} />
                                                    </button>
                                                </p>
                                                {w.chain === 'TRON' && w.is_multisig && w.relay_wallet_id && (
                                                    <p className="text-xs text-amber-500">
                                                        签名通过后先转入中转钱包，再由系统自动分发（gas 钱包自动补 TRX）
                                                    </p>
                                                )}
                                                {w.chain === 'BSC' && w.is_multisig && (
                                                    <p className="text-xs text-blue-500">
                                                        BSC 多签钱包：一次签名批量执行（Safe MultiSend）
                                                    </p>
                                                )}
                                            </div>
                                        )
                                    })()}
                                </div>
                            </div>

                            <div className="mt-4">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">批次备注</label>
                                <Input
                                    placeholder="本次打款备注（可选）"
                                    className="h-10"
                                    value={batchMemo}
                                    onChange={(e) => setBatchMemo(e.target.value)}
                                />
                            </div>
                        </div>

                        {/* 地址列表 */}
                        <div className="elegant-card bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35] overflow-hidden">
                            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-[#2a2d35]">
                                <span className="font-medium text-sm text-gray-700 dark:text-gray-300">
                                    打款明细 <span className="text-gray-400 font-normal">({batchItems.length} 笔)</span>
                                </span>
                                <div className="flex items-center gap-2">
                                    {/* 下载模板 */}
                                    <button
                                        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 dark:border-[#2a2d35] text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-[#22252e] cursor-pointer transition-colors"
                                        onClick={() => {
                                            const sample = batchChain === 'BSC'
                                                ? '0xAbc123...DEF456,100.00,用户A\n0x789XYZ...012ABC,50.50,用户B\n0xFEDCBA...654321,200.00,'
                                                : 'TAddr1exampleTRON1234567,100.00,用户A\nTAddr2exampleTRON7654321,50.50,用户B\nTAddr3exampleTRON9999999,200.00,'
                                            const blob = new Blob([`地址,金额,备注\n${sample}`], { type: 'text/csv;charset=utf-8;' })
                                            const url = URL.createObjectURL(blob)
                                            const a = document.createElement('a')
                                            a.href = url
                                            a.download = `payout_template_${batchChain}.csv`
                                            a.click()
                                            URL.revokeObjectURL(url)
                                        }}
                                    >
                                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                                        下载模板
                                    </button>
                                    {/* CSV 导入 */}
                                    <label className="cursor-pointer">
                                        <input
                                            type="file"
                                            accept=".csv,.txt"
                                            className="hidden"
                                            onChange={(e) => {
                                                const file = e.target.files?.[0]
                                                if (!file) return
                                                const reader = new FileReader()
                                                reader.onload = (ev) => {
                                                    const buf = ev.target?.result as ArrayBuffer
                                                    // 先尝试 UTF-8，若含乱码字符则回退 GBK（Excel 默认保存格式）
                                                    let text = new TextDecoder('utf-8').decode(buf)
                                                    if (text.includes('\uFFFD')) {
                                                        text = new TextDecoder('gb18030').decode(buf)
                                                    }
                                                    const lines = text.split('\n').filter(l => l.trim())
                                                    let counter = batchItemCounter
                                                    const newItems: BatchItem[] = lines.flatMap(line => {
                                                        const parts = line.split(',').map(p => p.trim())
                                                        const addr = parts[0] || ''
                                                        const amt = parts[1] || ''
                                                        const memo = parts[2] || ''
                                                        // 跳过标题行（首列不像地址）
                                                        if (!addr || addr === '地址' || addr.toLowerCase() === 'address') return []
                                                        return [{ id: counter++, address: addr, amount: amt, memo }]
                                                    })
                                                    if (newItems.length > 0) {
                                                        setBatchItems(prev => [...prev, ...newItems])
                                                        setBatchItemCounter(counter)
                                                    }
                                                    e.target.value = ''
                                                }
                                                reader.readAsArrayBuffer(file)
                                            }}
                                        />
                                        <span className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 dark:border-[#2a2d35] text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-[#22252e] cursor-pointer transition-colors">
                                            <Upload className="w-3.5 h-3.5" /> 导入 CSV
                                        </span>
                                    </label>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        className="gap-1.5 text-xs"
                                        onClick={() => {
                                            setBatchItems(prev => [...prev, { id: batchItemCounter, address: '', amount: '', memo: '' }])
                                            setBatchItemCounter(c => c + 1)
                                        }}
                                    >
                                        <Plus className="w-3.5 h-3.5" /> 添加行
                                    </Button>
                                </div>
                            </div>

                            {/* 表头 */}
                            <div className="grid grid-cols-[2fr_1fr_1fr_auto] gap-2 px-6 py-2 bg-gray-50 dark:bg-[#1c1f26] text-xs text-gray-500 dark:text-gray-400 font-medium">
                                <span>目标地址</span>
                                <span>金额 ({batchAssetType === 'usdt' ? 'USDT' : batchChain === 'BSC' ? 'BNB' : 'TRX'})</span>
                                <span>备注</span>
                                <span></span>
                            </div>

                            {/* 数据行 */}
                            <div className="divide-y divide-gray-50 dark:divide-[#22252e]">
                                {batchItems.map((item) => (
                                    <div key={item.id} className="grid grid-cols-[2fr_1fr_1fr_auto] gap-2 px-6 py-2 items-center">
                                        <Input
                                            placeholder={batchChain === 'BSC' ? '0x...' : 'T...'}
                                            className="h-9 font-mono text-xs"
                                            value={item.address}
                                            onChange={(e) => {
                                                const v = e.target.value
                                                setBatchItems(prev => prev.map(it => it.id === item.id ? { ...it, address: v } : it))
                                            }}
                                        />
                                        <Input
                                            type="number"
                                            placeholder="0.00"
                                            className="h-9 text-sm"
                                            value={item.amount}
                                            onChange={(e) => {
                                                const v = e.target.value
                                                setBatchItems(prev => prev.map(it => it.id === item.id ? { ...it, amount: v } : it))
                                            }}
                                        />
                                        <Input
                                            placeholder="备注"
                                            className="h-9 text-xs"
                                            value={item.memo}
                                            onChange={(e) => {
                                                const v = e.target.value
                                                setBatchItems(prev => prev.map(it => it.id === item.id ? { ...it, memo: v } : it))
                                            }}
                                        />
                                        <button
                                            className="p-1.5 text-gray-400 hover:text-red-400 transition-colors"
                                            onClick={() => {
                                                if (batchItems.length > 1) {
                                                    setBatchItems(prev => prev.filter(it => it.id !== item.id))
                                                }
                                            }}
                                            disabled={batchItems.length === 1}
                                        >
                                            <Trash2 className="w-3.5 h-3.5" />
                                        </button>
                                    </div>
                                ))}
                            </div>

                            {/* 合计 + 提交 */}
                            <div className="px-6 py-4 border-t border-gray-100 dark:border-[#2a2d35] flex items-center justify-between">
                                <div className="text-sm text-gray-500 dark:text-gray-400">
                                    合计:&nbsp;
                                    <span className="font-bold text-zinc-900 dark:text-white text-base">
                                        {batchItems.reduce((sum, it) => sum + (parseFloat(it.amount) || 0), 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                                    </span>
                                    &nbsp;{batchAssetType === 'usdt' ? 'USDT' : batchChain === 'BSC' ? 'BNB' : 'TRX'}
                                </div>
                                {batchError && (
                                    <span className="text-red-400 text-xs mr-4">{batchError}</span>
                                )}
                                <Button
                                    variant="primary"
                                    className="gap-2 min-w-[140px]"
                                    disabled={batchPrecheckLoading || batchPayoutWallets.length === 0}
                                    onClick={async () => {
                                        setBatchError('')
                                        if (!batchWalletId) { setBatchError('请选择打款钱包'); return }
                                        const validItems = batchItems.filter(it => it.address.trim() && parseFloat(it.amount) > 0)
                                        if (validItems.length === 0) { setBatchError('请至少添加一条有效打款记录'); return }

                                        const apiItems = validItems.map(it => ({
                                            to_address: it.address.trim(),
                                            amount: it.amount,
                                            memo: it.memo || undefined,
                                        }))
                                        setBatchSubmitItems(apiItems)
                                        setBatchPrecheckLoading(true)
                                        try {
                                            const res = await payoutApi.precheck({
                                                chain: batchChain,
                                                wallet_id: Number(batchWalletId),
                                                items: apiItems,
                                                asset_type: batchAssetType,
                                            })
                                            setBatchPrecheckResult(res.data)
                                            setBatchPrecheckOpen(true)
                                        } catch {
                                            setBatchError('余额预检失败，请稍后重试')
                                        } finally {
                                            setBatchPrecheckLoading(false)
                                        }
                                    }}
                                >
                                    {batchPrecheckLoading ? <><Loader2 className="w-4 h-4 animate-spin" /> 预检中...</> : <><Send className="w-4 h-4" /> 提交打款</>}
                                </Button>
                            </div>
                        </div>

                        <p className="text-xs text-gray-400 dark:text-gray-600">
                            CSV 格式：每行 <code className="bg-gray-100 dark:bg-[#22252e] px-1 rounded">地址,金额,备注</code>，第一行可为标题行（自动跳过），备注列可省略。点击「下载模板」获取示例文件。提交后需多签审批，签名达到阈值后系统自动执行。
                        </p>
                    </div>
                </TabsContent>

                {/* ─── 历史记录 ──────────────────────────── */}
                <TabsContent value="history">
                    {historyLoading ? (
                        <div className="flex items-center justify-center py-12 text-gray-400">
                            <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载中...
                        </div>
                    ) : (() => {
                        const batchStatusMap: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
                            pending:   { label: '待审批', variant: 'secondary' },
                            signing:   { label: '签名中', variant: 'warning' },
                            executing: { label: '执行中', variant: 'warning' },
                            completed: { label: '已完成', variant: 'success' },
                            partial:   { label: '部分完成', variant: 'warning' },
                            failed:    { label: '已失败', variant: 'destructive' },
                            cancelled: { label: '已取消', variant: 'secondary' },
                        }
                        const transferStatusMap: Record<string, { label: string; variant: 'warning' | 'success' | 'destructive' | 'secondary' }> = {
                            pending: { label: '待签名', variant: 'warning' },
                            signing: { label: '签名中', variant: 'warning' },
                            executed: { label: '已执行', variant: 'success' },
                            rejected: { label: '已取消', variant: 'destructive' },
                            expired: { label: '已过期', variant: 'secondary' },
                        }
                        const typeMap: Record<string, string> = { transfer: '内部转账', payout: '外部出款' }

                        // 批量打款分页
                        const batchTotalPages = Math.ceil(payoutBatches.length / batchPageSize) || 1
                        const batchRows = payoutBatches.slice((batchPage - 1) * batchPageSize, batchPage * batchPageSize)

                        // 内部转账+Gas直转分页
                        type UnifiedRow = { kind: 'proposal'; data: MultisigProposal; ts: number } | { kind: 'direct'; data: DirectTransferRecord; ts: number }
                        const transferRows: UnifiedRow[] = [
                            ...history.map(p => ({ kind: 'proposal' as const, data: p, ts: new Date(p.created_at).getTime() })),
                            ...directTransfers.map(d => ({ kind: 'direct' as const, data: d, ts: new Date(d.created_at).getTime() })),
                        ].sort((a, b) => b.ts - a.ts)
                        const transferTotalPages = Math.ceil(transferRows.length / transferPageSize) || 1
                        const transferPageRows = transferRows.slice((transferPage - 1) * transferPageSize, transferPage * transferPageSize)

                        const PageBar = ({ page, totalPages, onPrev, onNext, pageSize, onPageSize }: {
                            page: number; totalPages: number; onPrev: () => void; onNext: () => void
                            pageSize: number; onPageSize: (n: number) => void
                        }) => (
                            <div className="flex items-center justify-between mt-3">
                                <div className="flex items-center gap-2 text-xs text-gray-500">
                                    每页显示
                                    {[10, 20, 50].map(n => (
                                        <button key={n} onClick={() => { onPageSize(n) }}
                                            className={`px-2 py-0.5 rounded border text-xs transition-colors ${pageSize === n ? 'bg-blue-50 border-blue-300 text-blue-600 dark:bg-blue-500/10 dark:border-blue-500/30 dark:text-blue-400' : 'border-gray-200 dark:border-[#2a2d35] hover:bg-gray-50 dark:hover:bg-[#22252e]'}`}>
                                            {n}
                                        </button>
                                    ))}
                                    条
                                </div>
                                {totalPages > 1 && (
                                    <div className="flex items-center gap-2">
                                        <Button variant="outline" size="sm" disabled={page <= 1} onClick={onPrev}>上一页</Button>
                                        <span className="text-sm text-gray-500">{page} / {totalPages}</span>
                                        <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={onNext}>下一页</Button>
                                    </div>
                                )}
                            </div>
                        )

                        return (
                        <div className="flex flex-col gap-4">
                            {/* 子 Tab */}
                            <div className="flex gap-1 border-b border-gray-100 dark:border-[#2a2d35]">
                                {(['batches', 'transfers'] as const).map(tab => (
                                    <button key={tab} onClick={() => setHistorySubTab(tab)}
                                        className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${historySubTab === tab ? 'border-blue-500 text-blue-600 dark:text-blue-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}`}>
                                        {tab === 'batches' ? `批量打款 (${payoutBatches.length})` : `内部转账 / Gas 直转 (${transferRows.length})`}
                                    </button>
                                ))}
                            </div>

                            {/* 批量打款 Tab */}
                            {historySubTab === 'batches' && (
                            <div>
                                {batchRows.length === 0 ? (
                                    <div className="text-center py-12 text-gray-400 text-sm">暂无打款记录</div>
                                ) : (
                                <div className="rounded-xl border border-gray-100 dark:border-[#2a2d35] overflow-hidden">
                                    <table className="w-full text-sm">
                                        <thead>
                                            <tr className="bg-gray-50 dark:bg-[#22252e] text-xs text-gray-500 dark:text-gray-400">
                                                <th className="px-4 py-2.5 text-left font-medium">ID</th>
                                                <th className="px-4 py-2.5 text-left font-medium">网络</th>
                                                <th className="px-4 py-2.5 text-right font-medium">总金额</th>
                                                <th className="px-4 py-2.5 text-right font-medium">笔数</th>
                                                <th className="px-4 py-2.5 text-left font-medium">状态</th>
                                                <th className="px-4 py-2.5 text-left font-medium">时间</th>
                                                <th className="px-4 py-2.5"></th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {batchRows.map(b => {
                                                const bs = batchStatusMap[b.status] || { label: b.status, variant: 'secondary' as const }
                                                const tLabel = b.asset_type === 'native' ? (b.chain === 'BSC' ? 'BNB' : 'TRX') : 'USDT'
                                                return (
                                                    <tr key={b.id} className="border-t border-gray-100 dark:border-[#2a2d35] hover:bg-gray-50 dark:hover:bg-[#22252e]">
                                                        <td className="px-4 py-2.5 font-mono text-xs text-gray-500">#{b.id}</td>
                                                        <td className="px-4 py-2.5"><Badge variant="secondary" className="font-bold uppercase text-[10px]">{b.chain}</Badge></td>
                                                        <td className="px-4 py-2.5 text-right font-semibold dark:text-white">{parseFloat(b.total_amount).toLocaleString('en-US', { minimumFractionDigits: 2 })} {tLabel}</td>
                                                        <td className="px-4 py-2.5 text-right text-gray-600 dark:text-gray-400">{b.item_count} 笔</td>
                                                        <td className="px-4 py-2.5"><Badge variant={bs.variant} className="text-[10px]">{bs.label}</Badge></td>
                                                        <td className="px-4 py-2.5 text-xs text-gray-500 whitespace-nowrap">{new Date(b.created_at).toLocaleString('zh-CN')}</td>
                                                        <td className="px-4 py-2.5">
                                                            <Link to="/payouts/$id" params={{ id: String(b.id) }}>
                                                                <Button variant="ghost" size="sm" className="text-xs text-blue-600 dark:text-blue-400">详情</Button>
                                                            </Link>
                                                        </td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                                )}
                                <PageBar page={batchPage} totalPages={batchTotalPages}
                                    onPrev={() => setBatchPage(p => p - 1)} onNext={() => setBatchPage(p => p + 1)}
                                    pageSize={batchPageSize} onPageSize={n => { setBatchPageSize(n); setBatchPage(1) }} />
                            </div>
                            )}

                            {/* 内部转账 & Gas 直转 Tab */}
                            {historySubTab === 'transfers' && (
                            <div>
                                {transferPageRows.length === 0 ? (
                                    <div className="text-center py-12 text-gray-400 text-sm">暂无记录</div>
                                ) : (
                                <div className="rounded-xl border border-gray-100 dark:border-[#2a2d35] overflow-hidden">
                                    <table className="w-full text-sm">
                                        <thead>
                                            <tr className="bg-gray-50 dark:bg-[#22252e] text-xs text-gray-500 dark:text-gray-400">
                                                <th className="px-4 py-2.5 text-left font-medium">时间</th>
                                                <th className="px-4 py-2.5 text-left font-medium">类型</th>
                                                <th className="px-4 py-2.5 text-left font-medium">网络</th>
                                                <th className="px-4 py-2.5 text-left font-medium">目标地址</th>
                                                <th className="px-4 py-2.5 text-right font-medium">金额</th>
                                                <th className="px-4 py-2.5 text-left font-medium">状态 / TxHash</th>
                                                <th className="px-4 py-2.5"></th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {transferPageRows.map(row => {
                                                if (row.kind === 'direct') {
                                                    const r = row.data
                                                    const explorerUrl = r.chain === 'BSC' ? `https://bscscan.com/tx/${r.tx_hash}` : `https://tronscan.org/#/transaction/${r.tx_hash}`
                                                    return (
                                                        <tr key={`d-${r.id}`} className="border-t border-gray-100 dark:border-[#2a2d35] hover:bg-gray-50 dark:hover:bg-[#22252e]">
                                                            <td className="px-4 py-2.5 text-xs text-gray-500 whitespace-nowrap">{new Date(r.created_at).toLocaleString('zh-CN')}</td>
                                                            <td className="px-4 py-2.5"><Badge variant="secondary" className="text-[10px]">Gas 直转</Badge></td>
                                                            <td className="px-4 py-2.5"><Badge variant="secondary" className="font-bold uppercase text-[10px]">{r.chain}</Badge></td>
                                                            <td className="px-4 py-2.5 font-mono text-xs">{r.to_address ? `${r.to_address.slice(0, 8)}...${r.to_address.slice(-6)}` : '-'}</td>
                                                            <td className="px-4 py-2.5 text-right font-semibold dark:text-white whitespace-nowrap">{parseFloat(r.amount || '0').toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 8 })} {r.token_label}</td>
                                                            <td className="px-4 py-2.5 font-mono text-xs text-blue-500">
                                                                {r.tx_hash ? <a href={explorerUrl} target="_blank" rel="noreferrer" className="hover:underline flex items-center gap-1">{r.tx_hash.slice(0, 10)}... <ExternalLink className="w-3 h-3" /></a> : <Badge variant="secondary" className="text-[10px]">已广播</Badge>}
                                                            </td>
                                                            <td className="px-4 py-2.5"></td>
                                                        </tr>
                                                    )
                                                } else {
                                                    const p = row.data
                                                    const si = transferStatusMap[p.status] || { label: p.status, variant: 'secondary' as const }
                                                    const token = p.tx_data?._token as string | undefined
                                                    const tLabel = token === 'native' ? (p.chain === 'BSC' ? 'BNB' : 'TRX') : 'USDT'
                                                    return (
                                                        <tr key={`p-${p.id}`} className="border-t border-gray-100 dark:border-[#2a2d35] hover:bg-gray-50 dark:hover:bg-[#22252e]">
                                                            <td className="px-4 py-2.5 text-xs text-gray-500 whitespace-nowrap">{new Date(p.created_at).toLocaleString('zh-CN')}</td>
                                                            <td className="px-4 py-2.5"><Badge variant="secondary" className="text-[10px]">{typeMap[p.type] || p.type}</Badge></td>
                                                            <td className="px-4 py-2.5"><Badge variant="secondary" className="font-bold uppercase text-[10px]">{p.chain}</Badge></td>
                                                            <td className="px-4 py-2.5 font-mono text-xs">{p.to_address ? `${p.to_address.slice(0, 8)}...${p.to_address.slice(-6)}` : '-'}</td>
                                                            <td className="px-4 py-2.5 text-right font-semibold dark:text-white whitespace-nowrap">{p.amount ? parseFloat(p.amount).toLocaleString('en-US', { minimumFractionDigits: 2 }) : '-'} {tLabel}</td>
                                                            <td className="px-4 py-2.5">
                                                                <Badge variant={si.variant} className="text-[10px]">
                                                                    {(p.status === 'pending' || p.status === 'signing') && <Clock className="w-3 h-3 mr-1" />}
                                                                    {si.label} {p.current_signatures}/{p.threshold}
                                                                </Badge>
                                                            </td>
                                                            <td className="px-4 py-2.5">
                                                                <Button variant="ghost" size="sm" className="text-xs" onClick={() => handleViewDetail(p)}>
                                                                    <ExternalLink className="w-3 h-3 mr-1" /> 查看
                                                                </Button>
                                                            </td>
                                                        </tr>
                                                    )
                                                }
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                                )}
                                <PageBar page={transferPage} totalPages={transferTotalPages}
                                    onPrev={() => setTransferPage(p => p - 1)} onNext={() => setTransferPage(p => p + 1)}
                                    pageSize={transferPageSize} onPageSize={n => { setTransferPageSize(n); setTransferPage(1) }} />
                            </div>
                            )}
                        </div>
                        )
                    })()}
                </TabsContent>
            </Tabs>

            {/* ─── 确认弹窗 ──────────────────────────────── */}
            <Modal isOpen={confirmModalOpen} onClose={() => { if (!isSubmitting) setConfirmModalOpen(false) }} title="确认操作">
                <div className="flex flex-col gap-6">
                    {submitSuccess ? (
                        <div className="flex flex-col items-center py-6 animate-in zoom-in-75 duration-300">
                            <div className="w-16 h-16 bg-emerald-50 dark:bg-emerald-500/10 rounded-full flex items-center justify-center mb-4">
                                <CheckCircle2 className="w-8 h-8 text-emerald-500" />
                            </div>
                            <h3 className="text-lg font-bold text-zinc-900 dark:text-white">
                                {confirmData.fromWallet?.type === 'gas' ? '转账已广播' : '提案创建成功'}
                            </h3>
                            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                                {confirmData.fromWallet?.type === 'gas' ? '交易已上链...' : '即将跳转到签名中心...'}
                            </p>
                        </div>
                    ) : (
                        <>
                            <div className="bg-amber-50 dark:bg-amber-900/10 border border-amber-100 dark:border-amber-900/30 p-4 rounded-xl text-amber-800 dark:text-amber-200 text-sm mb-2">
                                {confirmData.fromWallet?.type === 'gas'
                                    ? 'Gas 钱包直接广播，无需多签，确认后立即执行。'
                                    : '此操作将创建一个多签提案，执行前需要达成签名共识。'
                                }
                            </div>
                            <div className="bg-gray-50 dark:bg-[#2a2d35] p-4 rounded-xl space-y-3">
                                <div className="flex justify-between">
                                    <span className="text-gray-500 dark:text-gray-400">操作类型</span>
                                    <span className="font-medium dark:text-white">{confirmData.type}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500 dark:text-gray-400">网络 / 代币</span>
                                    <span className="font-medium dark:text-white">{confirmData.chain} — {confirmData.tokenLabel}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500 dark:text-gray-400">来源钱包</span>
                                    <span className="font-mono text-xs dark:text-white truncate max-w-[200px]">
                                        {confirmData.fromWallet?.label && <span className="text-gray-400 mr-1">{confirmData.fromWallet.label}</span>}
                                        {confirmData.fromWallet?.address
                                            ? `${confirmData.fromWallet.address.slice(0, 8)}...${confirmData.fromWallet.address.slice(-6)}`
                                            : '-'}
                                    </span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500 dark:text-gray-400">目标地址</span>
                                    <span className="font-medium font-mono text-xs dark:text-white truncate max-w-[200px]">
                                        {confirmData.address
                                            ? `${confirmData.address.slice(0, 8)}...${confirmData.address.slice(-6)}`
                                            : '-'}
                                    </span>
                                </div>
                                <div className="flex justify-between pt-3 border-t border-gray-200 dark:border-[#3a3e47]">
                                    <span className="text-gray-500 dark:text-gray-400">金额</span>
                                    <span className="font-bold text-zinc-900 dark:text-white text-lg">
                                        {parseFloat(confirmData.amount || '0').toLocaleString('en-US', { minimumFractionDigits: 2 })} {confirmData.tokenLabel}
                                    </span>
                                </div>
                            </div>

                            <div className="flex items-center gap-3">
                                <Button variant="outline" className="flex-1" onClick={() => setConfirmModalOpen(false)} disabled={isSubmitting}>取消</Button>
                                <Button variant="primary" className="flex-1 gap-2" onClick={handleConfirmSubmit} disabled={isSubmitting}>
                                    {isSubmitting ? <><Loader2 className="w-4 h-4 animate-spin" /> 提交中...</> : confirmData.fromWallet?.type === 'gas' ? '确认转账' : '确认提案'}
                                </Button>
                            </div>
                        </>
                    )}
                </div>
            </Modal>

            {/* ─── 批量打款预检弹窗 ────────────────────────── */}
            <Modal isOpen={batchPrecheckOpen} onClose={() => { if (!batchSubmitting) setBatchPrecheckOpen(false) }} title="转账预检">
                {batchPrecheckResult && (
                    <div className="flex flex-col gap-4">
                        {/* 摘要 */}
                        <div className="bg-gray-50 dark:bg-[#22252e] rounded-xl p-4 flex flex-col gap-2">
                            <div className="flex justify-between text-sm">
                                <span className="text-gray-500 dark:text-gray-400">打款笔数</span>
                                <span className="font-medium dark:text-white">{batchSubmitItems.length} 笔</span>
                            </div>
                            <div className="flex justify-between text-sm">
                                <span className="text-gray-500 dark:text-gray-400">总转账额</span>
                                <span className="font-bold text-zinc-900 dark:text-white text-base">
                                    {Number(batchPrecheckResult.total_amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}
                                    {' '}{batchAssetType === 'usdt' ? 'USDT' : batchChain === 'BSC' ? 'BNB' : 'TRX'}
                                </span>
                            </div>
                        </div>

                        {/* USDT 余额检查（仅 USDT 打款时） */}
                        {batchAssetType === 'usdt' && (
                            <CheckRow
                                label="USDT 余额"
                                needed={`${Number(batchPrecheckResult.total_amount).toLocaleString('en-US', { minimumFractionDigits: 2 })} USDT`}
                                actual={`${Number(batchPrecheckResult.usdt_balance).toLocaleString('en-US', { minimumFractionDigits: 2 })} USDT`}
                                ok={batchPrecheckResult.usdt_sufficient}
                            />
                        )}

                        {/* Native 余额检查 */}
                        <CheckRow
                            label={
                                batchPrecheckResult.gas_auto_supplement
                                    ? 'TRX 余额（Gas 钱包，将自动补充）'
                                    : `${batchChain === 'BSC' ? 'BNB' : 'TRX'} 余额（Gas${batchAssetType === 'native' ? ' + 转账金额' : ''}）`
                            }
                            needed={`${Number(batchPrecheckResult.estimated_gas_native).toLocaleString('en-US', { minimumFractionDigits: 6, maximumFractionDigits: 6 })} ${batchChain === 'BSC' ? 'BNB' : 'TRX'}`}
                            actual={`${Number(batchPrecheckResult.native_balance).toLocaleString('en-US', { minimumFractionDigits: 6, maximumFractionDigits: 6 })} ${batchChain === 'BSC' ? 'BNB' : 'TRX'}`}
                            ok={batchPrecheckResult.native_sufficient}
                        />

                        {/* feee.io 能量租赁账户余额 */}
                        {batchPrecheckResult.feee_balance_trx !== null && (() => {
                            const feeeNeeded = batchPrecheckResult.estimated_feee_cost_trx != null
                                ? Number(batchPrecheckResult.estimated_feee_cost_trx).toFixed(2)
                                : (batchItems.filter(i => i.address).length * 75000 * 420 / 1_000_000).toFixed(2)
                            const neededLabel = batchPrecheckResult.estimated_feee_cost_trx != null ? '预估租赁费' : '预估上限'
                            return (
                                <CheckRow
                                    label="feee.io 租赁账户余额"
                                    needed={`${feeeNeeded} TRX（${neededLabel}）`}
                                    actual={`${Number(batchPrecheckResult.feee_balance_trx).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} TRX`}
                                    ok={batchPrecheckResult.feee_balance_sufficient !== false}
                                />
                            )
                        })()}

                        {/* TRON 能量说明 */}
                        {batchChain === 'TRON' && batchAssetType === 'usdt' && (
                            <div className="text-xs text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10 rounded-lg p-3">
                                {batchPrecheckResult.estimated_energy_cost_trx !== null
                                    ? `未启用能量租赁，预估每笔约 ${(Number(batchPrecheckResult.estimated_energy_cost_trx) / (batchItems.filter(i => i.address).length || 1)).toFixed(1)} TRX 能量费，建议在系统设置中启用以降低费用。`
                                    : '已启用能量租赁，能量费由租赁服务承担，Gas 钱包仅需补充带宽 TRX（约 0.35 TRX/笔）。'}
                                {batchPrecheckResult.gas_auto_supplement && ' TRX 将由系统 Gas 钱包自动补充。'}
                            </div>
                        )}

                        {/* 余额不足警告 */}
                        {!batchPrecheckResult.ok && (
                            <div className="bg-red-50 dark:bg-red-900/10 border border-red-100 dark:border-red-900/30 p-3 rounded-xl text-red-600 dark:text-red-400 text-sm flex items-start gap-2">
                                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                                <span>
                                    {batchPrecheckResult.feee_balance_sufficient === false
                                        ? 'feee.io 账户余额不足，请先向 feee.io 充值。'
                                        : batchPrecheckResult.gas_auto_supplement
                                            ? 'Gas 钱包 TRX 余额不足，请先向 Gas 钱包充值 TRX。'
                                            : '余额不足，执行时可能失败。请先向打款钱包充值后再提交。'}
                                </span>
                            </div>
                        )}

                        <div className="flex gap-3 pt-1">
                            <Button variant="outline" className="flex-1" onClick={() => setBatchPrecheckOpen(false)} disabled={batchSubmitting}>
                                取消
                            </Button>
                            <Button
                                variant="primary"
                                className="flex-1 gap-2"
                                disabled={!batchPrecheckResult.ok || batchSubmitting}
                                onClick={async () => {
                                    setBatchSubmitting(true)
                                    try {
                                        const res = await payoutApi.create({
                                            chain: batchChain,
                                            asset_type: batchAssetType,
                                            wallet_id: Number(batchWalletId),
                                            items: batchSubmitItems,
                                            memo: batchMemo || undefined,
                                        })
                                        toast.success(`打款批次 #${res.data.id} 已创建，等待多签审批`)
                                        setBatchPrecheckOpen(false)
                                        navigate({ to: '/payouts/$id', params: { id: String(res.data.id) } })
                                    } catch (e: unknown) {
                                        const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '创建失败'
                                        setBatchError(msg)
                                        setBatchPrecheckOpen(false)
                                    } finally {
                                        setBatchSubmitting(false)
                                    }
                                }}
                            >
                                {batchSubmitting ? <><Loader2 className="w-4 h-4 animate-spin" /> 提交中...</> : <><Send className="w-4 h-4" /> 确认提交</>}
                            </Button>
                        </div>
                    </div>
                )}
            </Modal>

            {/* ─── 详情弹窗 ──────────────────────────────── */}
            <Modal isOpen={detailOpen} onClose={() => setDetailOpen(false)} title="提案详情">
                {detailLoading ? (
                    <div className="flex items-center justify-center py-12 text-gray-400">
                        <Loader2 className="w-5 h-5 animate-spin mr-2" /> 加载中...
                    </div>
                ) : detailProposal && (
                    <div className="flex flex-col gap-4">
                        {/* 基本信息 */}
                        <div className="space-y-3">
                            <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                <span className="text-gray-500 text-sm">提案 ID</span>
                                <span className="font-mono text-sm font-medium dark:text-white">#{detailProposal.id}</span>
                            </div>
                            <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                <span className="text-gray-500 text-sm">类型 / 网络</span>
                                <span className="text-sm font-medium dark:text-white">
                                    {detailProposal.type === 'transfer' ? '内部转账' : detailProposal.type === 'payout_batch' ? '批量打款' : '外部出款'} / {detailProposal.chain}
                                </span>
                            </div>
                            <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                <span className="text-gray-500 text-sm">状态</span>
                                <Badge variant={
                                    detailProposal.status === 'executed' ? 'success'
                                        : detailProposal.status === 'rejected' ? 'destructive'
                                            : detailProposal.status === 'expired' ? 'secondary' : 'warning'
                                }>
                                    {detailProposal.status === 'executed' ? '已执行'
                                        : detailProposal.status === 'rejected' ? '已取消'
                                            : detailProposal.status === 'expired' ? '已过期'
                                                : detailProposal.status === 'signing' ? '签名中' : '待签名'}
                                </Badge>
                            </div>

                            {/* 金额 */}
                            <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                <span className="text-gray-500 text-sm">金额</span>
                                <span className="font-bold text-lg dark:text-white">
                                    {detailProposal.amount ? parseFloat(detailProposal.amount).toLocaleString('en-US', { minimumFractionDigits: 2 }) : '-'}
                                    {' '}
                                    <span className="text-sm font-normal text-gray-500">
                                        {(() => {
                                            const token = detailProposal.tx_data?._token as string | undefined
                                            return token === 'native' ? (detailProposal.chain === 'BSC' ? 'BNB' : 'TRX') : 'USDT'
                                        })()}
                                    </span>
                                </span>
                            </div>

                            {/* 来源钱包 */}
                            <div className="flex justify-between items-center p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                <span className="text-gray-500 text-sm shrink-0 mr-4">来源钱包</span>
                                <span className="font-mono text-xs break-all text-right dark:text-white">{detailProposal.wallet_address || '-'}</span>
                            </div>

                            {/* 目标地址 */}
                            <div className="flex justify-between items-center p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                <span className="text-gray-500 text-sm shrink-0 mr-4">目标地址</span>
                                <span className="font-mono text-xs break-all text-right dark:text-white">{detailProposal.to_address || '-'}</span>
                            </div>

                            {/* 备注 */}
                            {proposalMemo
                                ? <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                    <span className="text-gray-500 text-sm">备注</span>
                                    <span className="text-sm dark:text-white">{proposalMemo}</span>
                                </div>
                                : null}

                            {/* 签名进度 */}
                            <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                <div className="flex justify-between mb-2">
                                    <span className="text-gray-500 text-sm">签名进度</span>
                                    <span className="text-sm font-bold dark:text-white">
                                        {detailProposal.current_signatures} / {detailProposal.threshold}
                                    </span>
                                </div>
                                <div className="w-full h-2 bg-gray-200 dark:bg-[#2a2d35] rounded-full">
                                    <div
                                        className={`h-full rounded-full transition-all ${
                                            detailProposal.current_signatures >= detailProposal.threshold
                                                ? 'bg-emerald-500' : 'bg-blue-500'
                                        }`}
                                        style={{ width: `${Math.min(100, (detailProposal.current_signatures / detailProposal.threshold) * 100)}%` }}
                                    />
                                </div>
                            </div>

                            {/* 签名者列表 */}
                            {detailProposal.signatures.length > 0 && (
                                <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                    <span className="text-gray-500 text-sm block mb-2">签名者</span>
                                    <div className="space-y-1">
                                        {detailProposal.signatures.map((s) => (
                                            <div key={s.id} className="flex items-center gap-2 text-xs py-1">
                                                <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
                                                <span className="font-mono text-gray-600 dark:text-gray-400">
                                                    {s.signer_address.slice(0, 8)}...{s.signer_address.slice(-6)}
                                                </span>
                                                {s.signer_username && (
                                                    <span className="text-gray-400">({s.signer_username})</span>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* 执行交易哈希 */}
                            {detailProposal.execution_tx_hash && (
                                <div className="p-3 bg-emerald-50 dark:bg-emerald-900/10 rounded-lg border border-emerald-100 dark:border-emerald-900/30">
                                    <span className="text-gray-500 text-sm block mb-1">执行交易哈希</span>
                                    <a
                                        href={detailProposal.chain === 'BSC'
                                            ? `https://bscscan.com/tx/${detailProposal.execution_tx_hash}`
                                            : `https://tronscan.org/#/transaction/${detailProposal.execution_tx_hash}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline break-all"
                                    >
                                        {detailProposal.execution_tx_hash}
                                    </a>
                                </div>
                            )}

                            {/* Gas 预转账记录（TRON） */}
                            {detailProposal.tx_data?._gas_tx_hash && (
                                <div className="p-3 bg-amber-50 dark:bg-amber-900/10 rounded-lg border border-amber-100 dark:border-amber-900/30">
                                    <span className="text-amber-700 dark:text-amber-400 text-sm font-medium block mb-2">Gas 预转账记录</span>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span className="text-gray-500">金额</span>
                                        <span className="font-medium dark:text-white">{String(detailProposal.tx_data._gas_amount)}</span>
                                    </div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span className="text-gray-500">来源</span>
                                        <span className="font-mono dark:text-white">{String(detailProposal.tx_data._gas_from || '').slice(0, 8)}...{String(detailProposal.tx_data._gas_from || '').slice(-6)}</span>
                                    </div>
                                    <div className="text-xs mt-1">
                                        <span className="text-gray-500 block mb-0.5">哈希</span>
                                        <a
                                            href={`https://tronscan.org/#/transaction/${detailProposal.tx_data._gas_tx_hash}`}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="font-mono text-blue-600 dark:text-blue-400 hover:underline break-all"
                                        >
                                            {String(detailProposal.tx_data._gas_tx_hash)}
                                        </a>
                                    </div>
                                </div>
                            )}

                            {/* 时间信息 */}
                            <div className="grid grid-cols-2 gap-3">
                                <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                    <span className="text-gray-500 text-xs block">创建时间</span>
                                    <span className="text-xs dark:text-white">
                                        {new Date(detailProposal.created_at).toLocaleString('zh-CN')}
                                    </span>
                                </div>
                                {detailProposal.executed_at && (
                                    <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                        <span className="text-gray-500 text-xs block">执行时间</span>
                                        <span className="text-xs dark:text-white">
                                            {new Date(detailProposal.executed_at).toLocaleString('zh-CN')}
                                        </span>
                                    </div>
                                )}
                                <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                    <span className="text-gray-500 text-xs block">创建人</span>
                                    <span className="text-xs dark:text-white">{detailProposal.created_by_username || '-'}</span>
                                </div>
                                {detailProposal.expires_at && (
                                    <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                        <span className="text-gray-500 text-xs block">过期时间</span>
                                        <span className="text-xs dark:text-white">
                                            {new Date(detailProposal.expires_at).toLocaleString('zh-CN')}
                                        </span>
                                    </div>
                                )}
                            </div>
                        </div>

                        <Button variant="outline" className="w-full" onClick={() => setDetailOpen(false)}>
                            关闭
                        </Button>
                    </div>
                )}
            </Modal>
        </div>
    )
}
