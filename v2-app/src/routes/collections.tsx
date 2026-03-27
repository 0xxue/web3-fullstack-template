import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState, useMemo, useCallback, useEffect } from 'react'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { DataTable } from '@/components/ui/data-table'
import { Modal } from '@/components/ui/modal'
import { Input } from '@/components/ui/input'
import { ScanSearch, CheckCircle2, ChevronRight, Activity, Loader2, RefreshCw, ArrowDownToLine } from 'lucide-react'
import { ColumnDef } from '@tanstack/react-table'
import { Badge } from '@/components/ui/badge'
import { toast } from 'sonner'
import { Select } from '@/components/ui/select'
import { collectionApi } from '@/lib/api'

// ─── Types ───────────────────────────────────────────

type ScannedAddress = {
    address: string
    derive_index: number
    balance: number
    native_balance: number
    gas_needed: number
    gas_sufficient: boolean
    label: string | null
}

type CollectionWallet = {
    id: number
    address: string
    label: string | null
    is_multisig: boolean
    multisig_status: string | null
}

type CollectionRecord = {
    id: number
    chain: string
    status: string
    total_amount: string
    address_count: number
    created_by: number
    executed_at: string | null
    created_at: string
    updated_at: string
}

// ─── Helpers ─────────────────────────────────────────

function formatAmount(val: number | string) {
    const num = typeof val === 'string' ? parseFloat(val) : val
    if (isNaN(num)) return '0.00'
    return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatTime(iso: string) {
    try {
        const d = new Date(iso)
        const now = new Date()
        const diffMs = now.getTime() - d.getTime()
        if (diffMs < 60_000) return '刚刚'
        if (diffMs < 3600_000) return `${Math.floor(diffMs / 60_000)} 分钟前`
        const isToday = d.toDateString() === now.toDateString()
        const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
        if (isToday) return `今天 ${time}`
        const yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1)
        if (d.toDateString() === yesterday.toDateString()) return `昨天 ${time}`
        return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' + time
    } catch {
        return iso
    }
}

function explorerAddrUrl(chain: string, addr: string) {
    if (chain === 'BSC') return `https://bscscan.com/address/${addr}`
    return `https://tronscan.org/#/address/${addr}`
}

const statusMap: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
    executing: { label: '执行中', variant: 'warning' },
    completed: { label: '已完成', variant: 'success' },
    partial: { label: '部分完成', variant: 'warning' },
    failed: { label: '已失败', variant: 'destructive' },
    pending: { label: '待签名', variant: 'secondary' },
    signing: { label: '签名中', variant: 'warning' },
    cancelled: { label: '已取消', variant: 'secondary' },
}

// ─── Component ───────────────────────────────────────

export const Route = createFileRoute('/collections')({
    component: CollectionsComponent,
})

function CollectionsComponent() {
    const navigate = useNavigate()
    const [activeTab, setActiveTab] = useState('new')
    const [isScanning, setIsScanning] = useState(false)
    const [scanComplete, setScanComplete] = useState(false)
    const [confirmModalOpen, setConfirmModalOpen] = useState(false)
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [customTotalAmount, setCustomTotalAmount] = useState('')
    const [scanChain, setScanChain] = useState('BSC')
    const [threshold, setThreshold] = useState('1')
    const [assetType, setAssetType] = useState<'usdt' | 'native'>('usdt')
    const [collectionWallets, setCollectionWallets] = useState<CollectionWallet[]>([])
    const [selectedWalletId, setSelectedWalletId] = useState<number | null>(null)

    // Scan results
    const [scanResults, setScanResults] = useState<ScannedAddress[]>([])

    // History
    const [historyData, setHistoryData] = useState<CollectionRecord[]>([])
    const [historyTotal, setHistoryTotal] = useState(0)
    const [historyPage, setHistoryPage] = useState(1)
    const [historyLoading, setHistoryLoading] = useState(false)

    // ─── 归集目标钱包 ──────────────────────────────────

    useEffect(() => {
        collectionApi.listWallets(scanChain).then(({ data }) => {
            setCollectionWallets(data || [])
            setSelectedWalletId((data && data.length > 0) ? data[0].id : null)
        }).catch(() => {
            setCollectionWallets([])
            setSelectedWalletId(null)
        })
    }, [scanChain])

    const selectedWallet = collectionWallets.find(w => w.id === selectedWalletId) || null

    // ─── Scan ─────────────────────────────────────────

    const handleScan = async () => {
        setIsScanning(true)
        setScanComplete(false)
        setSelectedIds(new Set())
        setScanResults([])
        setCustomTotalAmount('')
        try {
            const minAmount = Number(threshold) || 0
            const { data } = await collectionApi.scan({
                chain: scanChain,
                min_amount: minAmount,
                asset_type: assetType,
            })
            const addresses: ScannedAddress[] = (data.addresses || []).map((a: any) => ({
                ...a,
                balance: Number(a.balance),
                native_balance: Number(a.native_balance),
                gas_needed: Number(a.gas_needed),
            }))
            setScanResults(addresses)
            setScanComplete(true)
            // Auto-select all addresses with balance
            const readyIds = new Set(
                addresses.filter((a) => a.balance > 0).map((a) => a.address)
            )
            setSelectedIds(readyIds)
        } catch (err: any) {
            toast.error(err?.response?.data?.detail || '扫描失败，请检查网络和 RPC 配置')
        } finally {
            setIsScanning(false)
        }
    }

    // ─── Selection ────────────────────────────────────

    const toggleSelect = (address: string) => {
        setSelectedIds((prev) => {
            const next = new Set(prev)
            if (next.has(address)) next.delete(address)
            else next.add(address)
            return next
        })
    }

    const toggleSelectAll = () => {
        if (selectedIds.size === scanResults.length) {
            setSelectedIds(new Set())
        } else {
            setSelectedIds(new Set(scanResults.map((a) => a.address)))
        }
    }

    const selectedAddresses = useMemo(() =>
        scanResults.filter((a) => selectedIds.has(a.address)),
        [selectedIds, scanResults]
    )

    const actualTotal = useMemo(() =>
        selectedAddresses.reduce((sum, a) => sum + a.balance, 0),
        [selectedAddresses]
    )

    const totalAmount = useMemo(() => formatAmount(actualTotal), [actualTotal])

    const customTotal = customTotalAmount ? parseFloat(customTotalAmount) : null
    const isCustomValid = customTotal === null || (customTotal > 0 && customTotal <= actualTotal)
    const effectiveTotal = (customTotal && customTotal < actualTotal) ? customTotal : actualTotal
    const collectRatio = actualTotal > 0 ? effectiveTotal / actualTotal : 1

    // ─── Submit ───────────────────────────────────────

    const handleSubmitCollection = async () => {
        setIsSubmitting(true)
        try {
            const addresses = selectedAddresses.map((a) => ({
                address: a.address,
                amount: parseFloat((a.balance * collectRatio).toFixed(6)),
            }))
            const resp = await collectionApi.create({ chain: scanChain, addresses, asset_type: assetType, wallet_id: selectedWalletId ?? undefined })
            const proposalId = resp.data?.proposal_id
            toast.success('归集提案已创建', {
                description: proposalId
                    ? `请到签名中心完成 2/3 多签审批（提案 #${proposalId}）`
                    : `${addresses.length} 个地址，共 ${totalAmount} USDT`,
            })
            setConfirmModalOpen(false)
            setScanComplete(false)
            setSelectedIds(new Set())
            setScanResults([])
            fetchHistory()
            setActiveTab('history')
        } catch (err: any) {
            toast.error(err?.response?.data?.detail || '提交失败')
        } finally {
            setIsSubmitting(false)
        }
    }

    // ─── History ──────────────────────────────────────

    const fetchHistory = useCallback(async () => {
        setHistoryLoading(true)
        try {
            const { data } = await collectionApi.list({
                page: historyPage,
                page_size: 20,
            })
            setHistoryData(data.items || [])
            setHistoryTotal(data.total || 0)
        } catch {
            // silent
        } finally {
            setHistoryLoading(false)
        }
    }, [historyPage])

    useEffect(() => { fetchHistory() }, [fetchHistory])

    // Auto-refresh when on history tab
    useEffect(() => {
        if (activeTab !== 'history') return
        const timer = setInterval(fetchHistory, 10000)
        return () => clearInterval(timer)
    }, [activeTab, fetchHistory])

    const handleViewDetail = (record: CollectionRecord) => {
        navigate({ to: '/collections/$id', params: { id: String(record.id) } })
    }

    // ─── Columns ──────────────────────────────────────

    const gasLabel = scanChain === 'BSC' ? 'BNB' : 'TRX'
    const tokenLabel = assetType === 'usdt' ? 'USDT' : gasLabel

    const scanColumns: ColumnDef<ScannedAddress>[] = [
        {
            id: 'select',
            header: () => (
                <input
                    type="checkbox"
                    className="rounded border-gray-300 text-blue-600 shadow-sm focus:border-blue-300 focus:ring focus:ring-blue-200 focus:ring-opacity-50"
                    checked={selectedIds.size === scanResults.length && scanResults.length > 0}
                    onChange={toggleSelectAll}
                />
            ),
            cell: ({ row }) => (
                <input
                    type="checkbox"
                    className="rounded border-gray-300 text-blue-600 shadow-sm focus:border-blue-300 focus:ring focus:ring-blue-200 focus:ring-opacity-50"
                    checked={selectedIds.has(row.original.address)}
                    onChange={() => toggleSelect(row.original.address)}
                />
            ),
        },
        {
            accessorKey: 'address',
            header: '地址',
            cell: ({ row }) => {
                const addr = row.original.address
                return (
                    <a
                        href={explorerAddrUrl(scanChain, addr)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-mono text-sm text-blue-600 dark:text-blue-400 hover:underline"
                        title={addr}
                    >
                        {addr.length > 16 ? `${addr.slice(0, 8)}...${addr.slice(-6)}` : addr}
                    </a>
                )
            },
        },
        {
            accessorKey: 'balance',
            header: `余额 (${tokenLabel})`,
            cell: ({ row }) => (
                <span className="font-semibold text-zinc-900 dark:text-gray-200">
                    {formatAmount(row.original.balance)}
                </span>
            ),
        },
        ...(assetType === 'usdt' ? [{
            accessorKey: 'gas_needed' as const,
            header: `预估 Gas (${gasLabel})`,
            cell: ({ row }: { row: { original: ScannedAddress } }) => (
                <span className="text-gray-500">
                    {row.original.gas_needed} {gasLabel}
                </span>
            ),
        },
        {
            id: 'status',
            header: '状态',
            cell: ({ row }: { row: { original: ScannedAddress } }) => {
                const ok = row.original.gas_sufficient
                return (
                    <Badge variant={ok ? 'success' : 'warning'}>
                        {ok ? '准备就绪' : '需要补充 Gas'}
                    </Badge>
                )
            },
        }] : []),
    ]

    const historyColumns: ColumnDef<CollectionRecord>[] = [
        {
            accessorKey: 'id',
            header: 'ID',
            cell: ({ row }) => <span className="font-mono text-xs">#{row.original.id}</span>,
        },
        {
            accessorKey: 'chain',
            header: '网络',
            cell: ({ row }) => (
                <Badge variant="secondary" className="font-bold uppercase text-[10px]">
                    {row.original.chain}
                </Badge>
            ),
        },
        {
            accessorKey: 'address_count',
            header: '地址数',
            cell: ({ row }) => `${row.original.address_count} 个`,
        },
        {
            accessorKey: 'total_amount',
            header: '总金额 (USDT)',
            cell: ({ row }) => (
                <span className="font-semibold text-emerald-600 dark:text-emerald-400">
                    {formatAmount(row.original.total_amount)}
                </span>
            ),
        },
        {
            accessorKey: 'status',
            header: '状态',
            cell: ({ row }) => {
                const s = statusMap[row.original.status] || { label: row.original.status, variant: 'secondary' as const }
                return <Badge variant={s.variant}>{s.label}</Badge>
            },
        },
        {
            accessorKey: 'created_at',
            header: '时间',
            cell: ({ row }) => (
                <span className="text-gray-500 whitespace-nowrap">
                    {formatTime(row.original.created_at)}
                </span>
            ),
        },
        {
            id: 'actions',
            cell: ({ row }) => (
                <Button
                    variant="ghost"
                    size="sm"
                    className="text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-500/10"
                    onClick={() => handleViewDetail(row.original)}
                >
                    详情
                </Button>
            ),
        },
    ]

    return (
        <div className="w-full flex flex-col gap-6">
            <div>
                <h1 className="text-2xl font-bold text-zinc-900 dark:text-white mb-1">资金归集管理</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">将用户充值地址中的资金统一归集到主资金库。</p>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="new">发起归集</TabsTrigger>
                    <TabsTrigger value="history">历史记录</TabsTrigger>
                </TabsList>

                <TabsContent value="new">
                    <div className="flex flex-col gap-6">
                        <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                            <div className="flex flex-col md:flex-row md:items-end gap-6">
                                <div className="flex-1">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">选择网络</label>
                                    <Select
                                        value={scanChain}
                                        onChange={setScanChain}
                                        options={[
                                            { value: 'BSC', label: 'BSC (BNB Smart Chain)' },
                                            { value: 'TRON', label: 'TRON' },
                                        ]}
                                    />
                                </div>
                                <div className="flex-1">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">归集代币</label>
                                    <Select
                                        value={assetType}
                                        onChange={(v) => setAssetType(v as 'usdt' | 'native')}
                                        options={[
                                            { value: 'usdt', label: 'USDT' },
                                            { value: 'native', label: scanChain === 'BSC' ? 'BNB' : 'TRX' },
                                        ]}
                                    />
                                </div>
                                <div className="flex-1">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">归集目标钱包</label>
                                    {collectionWallets.length > 0 ? (
                                        <Select
                                            value={String(selectedWalletId ?? '')}
                                            onChange={(v) => setSelectedWalletId(Number(v))}
                                            options={collectionWallets.map(w => ({
                                                value: String(w.id),
                                                label: `${w.label || '归集钱包'} — ${w.address.slice(0, 8)}...${w.address.slice(-6)}`,
                                            }))}
                                        />
                                    ) : (
                                        <div className="h-10 flex items-center text-sm text-red-500">未配置归集钱包</div>
                                    )}
                                </div>
                                <div className="flex-1">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">最低金额</label>
                                    <Input
                                        type="number"
                                        placeholder="如 1, 5, 100..."
                                        value={threshold}
                                        onChange={(e) => setThreshold(e.target.value)}
                                    />
                                </div>
                                <Button
                                    variant="primary"
                                    size="lg"
                                    className="gap-2 shrink-0 md:w-auto w-full"
                                    onClick={handleScan}
                                    disabled={isScanning}
                                >
                                    {isScanning ? (
                                        <><Activity className="w-4 h-4 animate-spin" /> 正在扫描...</>
                                    ) : (
                                        <><ScanSearch className="w-4 h-4" /> 开始扫描</>
                                    )}
                                </Button>
                            </div>
                        </div>

                        {scanComplete && (
                            <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white flex items-center gap-2">
                                        <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                                        扫描完成
                                    </h3>
                                    <span className="text-sm text-gray-500 bg-gray-100 dark:bg-[#2a2d35] px-3 py-1 rounded-full">
                                        发现 {scanResults.length} 个符合条件的地址
                                    </span>
                                </div>

                                <DataTable columns={scanColumns} data={scanResults} />

                                {/* Submittal Bar */}
                                <div className="mt-6 flex flex-col p-4 px-6 bg-blue-50 dark:bg-blue-500/10 border border-blue-100 dark:border-blue-900/30 rounded-xl gap-4">
                                    <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                                        <div className="flex-1 w-full">
                                            <div className="text-sm text-blue-600 dark:text-blue-400 font-medium">已选择 {selectedIds.size} 个地址</div>
                                            <div className="text-2xl font-bold text-zinc-900 dark:text-white mt-0.5">
                                                {customTotal && customTotal < actualTotal
                                                    ? <>{formatAmount(customTotal)} <span className="text-sm text-gray-400 font-normal line-through mr-1">{totalAmount}</span></>
                                                    : totalAmount
                                                }
                                                {' '}<span className="text-sm text-gray-500 font-normal">{tokenLabel} 总计</span>
                                            </div>
                                            <div className="flex items-center gap-2 mt-2">
                                                <ArrowDownToLine className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                                                <span className="text-xs text-gray-500 dark:text-gray-400 shrink-0">归集到：</span>
                                                {collectionWallets.length > 1 ? (
                                                    <Select
                                                        value={String(selectedWalletId ?? '')}
                                                        onChange={(v) => setSelectedWalletId(Number(v))}
                                                        options={collectionWallets.map(w => ({
                                                            value: String(w.id),
                                                            label: `${w.label || '归集钱包'} — ${w.address}`,
                                                        }))}
                                                        className="flex-1 min-w-0"
                                                    />
                                                ) : selectedWallet ? (
                                                    <a
                                                        href={explorerAddrUrl(scanChain, selectedWallet.address)}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="text-sm font-mono font-semibold text-blue-600 dark:text-blue-400 hover:underline truncate"
                                                        title={selectedWallet.address}
                                                    >
                                                        {selectedWallet.label ? `${selectedWallet.label} — ` : ''}{selectedWallet.address}
                                                    </a>
                                                ) : (
                                                    <span className="text-xs text-red-500">未配置归集钱包</span>
                                                )}
                                            </div>
                                        </div>
                                        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full sm:w-auto shrink-0">
                                            <div className="flex flex-col gap-1 sm:w-44">
                                                <label className="text-xs text-gray-500 dark:text-gray-400">自定义归集金额（可选）</label>
                                                <Input
                                                    type="number"
                                                    placeholder={`最多 ${totalAmount}`}
                                                    value={customTotalAmount}
                                                    onChange={(e) => setCustomTotalAmount(e.target.value)}
                                                    className={!isCustomValid ? 'border-red-400 focus:border-red-400' : ''}
                                                />
                                                {!isCustomValid && (
                                                    <span className="text-xs text-red-500">不能超过 {totalAmount}</span>
                                                )}
                                            </div>
                                            <Button
                                                variant="primary"
                                                size="lg"
                                                className="w-full sm:w-auto gap-2 mt-auto"
                                                onClick={() => setConfirmModalOpen(true)}
                                                disabled={selectedIds.size === 0 || !isCustomValid}
                                            >
                                                发起资金归集 <ChevronRight className="w-4 h-4" />
                                            </Button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                </TabsContent>

                <TabsContent value="history">
                    <div className="flex justify-end mb-4">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={fetchHistory}
                            disabled={historyLoading}
                            className="gap-2"
                        >
                            <RefreshCw className={`w-4 h-4 ${historyLoading ? 'animate-spin' : ''}`} />
                            刷新
                        </Button>
                    </div>
                    <DataTable columns={historyColumns} data={historyData} />
                    {historyTotal > 20 && (
                        <div className="flex justify-center gap-2 mt-4">
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={historyPage <= 1}
                                onClick={() => setHistoryPage((p) => Math.max(1, p - 1))}
                            >
                                上一页
                            </Button>
                            <span className="text-sm text-gray-500 leading-8">
                                第 {historyPage} 页 / 共 {Math.ceil(historyTotal / 20)} 页
                            </span>
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={historyPage >= Math.ceil(historyTotal / 20)}
                                onClick={() => setHistoryPage((p) => p + 1)}
                            >
                                下一页
                            </Button>
                        </div>
                    )}
                </TabsContent>
            </Tabs>

            {/* Confirm Modal */}
            <Modal isOpen={confirmModalOpen} onClose={() => setConfirmModalOpen(false)} title="确认发起归集">
                <div className="flex flex-col gap-6">
                    <div className="bg-gray-50 dark:bg-[#2a2d35] p-4 rounded-xl space-y-3">
                        <div className="flex justify-between">
                            <span className="text-gray-500 dark:text-gray-400">网络</span>
                            <span className="font-medium dark:text-white">{scanChain}</span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-gray-500 dark:text-gray-400">地址数</span>
                            <span className="font-medium dark:text-white">{selectedIds.size}</span>
                        </div>
                        {selectedWallet && (
                            <div className="flex justify-between gap-4">
                                <span className="text-gray-500 dark:text-gray-400 shrink-0">归集目标</span>
                                <div className="text-right">
                                    {selectedWallet.label && (
                                        <div className="text-xs font-medium dark:text-white mb-0.5">{selectedWallet.label}</div>
                                    )}
                                    <a
                                        href={explorerAddrUrl(scanChain, selectedWallet.address)}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-xs font-mono text-blue-600 dark:text-blue-400 hover:underline break-all"
                                    >
                                        {selectedWallet.address}
                                    </a>
                                </div>
                            </div>
                        )}
                        <div className="flex justify-between pt-3 border-t border-gray-200 dark:border-[#3a3e47]">
                            <span className="text-gray-500 dark:text-gray-400">预计归集总额</span>
                            <span className="font-bold text-emerald-600 dark:text-emerald-400">
                                {formatAmount(effectiveTotal)} {tokenLabel}
                                {customTotal && customTotal < actualTotal && (
                                    <span className="text-xs text-gray-400 font-normal ml-1">（按比例归集）</span>
                                )}
                            </span>
                        </div>
                    </div>

                    <p className="text-sm text-gray-500 dark:text-gray-400">
                        提交后将创建归集提案，需要 2/3 多签钱包 Owner 在签名中心完成签名后自动执行归集。
                    </p>

                    <div className="flex items-center gap-3">
                        <Button variant="outline" className="flex-1" onClick={() => setConfirmModalOpen(false)} disabled={isSubmitting}>取消</Button>
                        <Button variant="primary" className="flex-1 gap-2" onClick={handleSubmitCollection} disabled={isSubmitting}>
                            {isSubmitting ? <><Loader2 className="w-4 h-4 animate-spin" /> 提交中...</> : '创建归集提案'}
                        </Button>
                    </div>
                </div>
            </Modal>

        </div>
    )
}
