import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState, useEffect, useCallback, useRef } from 'react'
import { DataTable } from '@/components/ui/data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ColumnDef } from '@tanstack/react-table'
import { ArrowLeft, ExternalLink, RefreshCw, Download } from 'lucide-react'
import { toast } from 'sonner'
import { payoutApi } from '@/lib/api'

// ─── Types ───────────────────────────────────────────

type PayoutItem = {
    id: number
    to_address: string
    amount: string
    memo: string | null
    status: string
    tx_hash: string | null
    error_message: string | null
    retry_count: number
    created_at: string
    updated_at: string
}

type PayoutDetail = {
    id: number
    chain: string
    asset_type: string
    status: string
    total_amount: string
    item_count: number
    wallet_id: number
    wallet_address: string | null
    memo: string | null
    proposal_id: number | null
    created_by: number
    created_by_username: string | null
    executed_at: string | null
    created_at: string
    updated_at: string
    items: PayoutItem[]
}

// ─── Helpers ─────────────────────────────────────────

function formatAmount(val: number | string) {
    const num = typeof val === 'string' ? parseFloat(val) : val
    if (isNaN(num)) return '0.00'
    return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
}

function formatTime(iso: string) {
    try {
        return new Date(iso).toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        })
    } catch {
        return iso
    }
}

function explorerTxUrl(chain: string, hash: string) {
    if (chain === 'BSC') return `https://bscscan.com/tx/${hash}`
    return `https://tronscan.org/#/transaction/${hash}`
}

function explorerAddrUrl(chain: string, addr: string) {
    if (chain === 'BSC') return `https://bscscan.com/address/${addr}`
    return `https://tronscan.org/#/address/${addr}`
}

const batchStatusMap: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
    pending:   { label: '待多签审批', variant: 'secondary' },
    signing:   { label: '签名中', variant: 'warning' },
    executing: { label: '执行中', variant: 'warning' },
    completed: { label: '已完成', variant: 'success' },
    partial:   { label: '部分完成', variant: 'warning' },
    failed:    { label: '已失败', variant: 'destructive' },
    cancelled: { label: '已取消', variant: 'secondary' },
}

const itemStatusMap: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
    pending:    { label: '待处理', variant: 'secondary' },
    processing: { label: '处理中', variant: 'warning' },
    completed:  { label: '已完成', variant: 'success' },
    failed:     { label: '失败', variant: 'destructive' },
}

// ─── Route ───────────────────────────────────────────

export const Route = createFileRoute('/payouts_/$id')({
    component: PayoutDetailPage,
})

function PayoutDetailPage() {
    const { id } = Route.useParams()
    const navigate = useNavigate()
    const [detail, setDetail] = useState<PayoutDetail | null>(null)
    const [loading, setLoading] = useState(true)
    const [downloading, setDownloading] = useState(false)

    const fetchDetail = useCallback(async () => {
        try {
            const { data } = await payoutApi.getDetail(Number(id))
            setDetail(data)
        } catch {
            toast.error('加载打款详情失败')
        } finally {
            setLoading(false)
        }
    }, [id])

    useEffect(() => { fetchDetail() }, [fetchDetail])

    // 非终态自动刷新
    const statusRef = useRef(detail?.status)
    statusRef.current = detail?.status

    useEffect(() => {
        const tick = () => {
            const s = statusRef.current
            if (s && !['completed', 'failed', 'cancelled'].includes(s)) {
                fetchDetail()
            }
        }
        const timer = setInterval(tick, 3000)
        return () => clearInterval(timer)
    }, [fetchDetail])

    const handleExportCsv = async (status?: string) => {
        if (!detail) return
        setDownloading(true)
        try {
            const res = await payoutApi.exportCsv(detail.id, status)
            const url = URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }))
            const a = document.createElement('a')
            a.href = url
            a.download = `payout_${detail.id}${status === 'failed' ? '_failed' : ''}.csv`
            a.click()
            URL.revokeObjectURL(url)
        } catch {
            toast.error('导出失败')
        } finally {
            setDownloading(false)
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <RefreshCw className="w-6 h-6 animate-spin text-gray-400" />
            </div>
        )
    }

    if (!detail) {
        return (
            <div className="text-center py-20 text-gray-500">
                打款记录不存在
                <Button variant="outline" className="ml-4" onClick={() => navigate({ to: '/payouts', search: { tab: 'history' } })}>
                    返回列表
                </Button>
            </div>
        )
    }

    const chain = detail.chain
    const tokenLabel = detail.asset_type === 'native' ? (chain === 'BSC' ? 'BNB' : 'TRX') : 'USDT'
    const batchStatus = batchStatusMap[detail.status] || { label: detail.status, variant: 'secondary' as const }

    const completedCount = (detail.items || []).filter(i => i.status === 'completed').length
    const failedCount = (detail.items || []).filter(i => i.status === 'failed').length
    const pendingCount = (detail.items || []).filter(i => !['completed', 'failed'].includes(i.status)).length

    const columns: ColumnDef<PayoutItem>[] = [
        {
            accessorKey: 'to_address',
            header: '目标地址',
            cell: ({ row }) => {
                const addr = row.original.to_address
                return (
                    <a
                        href={explorerAddrUrl(chain, addr)}
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
            accessorKey: 'amount',
            header: `金额 (${tokenLabel})`,
            cell: ({ row }) => (
                <span className="font-semibold text-zinc-900 dark:text-gray-200">
                    {formatAmount(row.original.amount)}
                </span>
            ),
        },
        {
            accessorKey: 'memo',
            header: '备注',
            cell: ({ row }) => (
                <span className="text-xs text-gray-500">{row.original.memo || '-'}</span>
            ),
        },
        {
            accessorKey: 'status',
            header: '状态',
            cell: ({ row }) => {
                const is = itemStatusMap[row.original.status] || itemStatusMap.pending
                return <Badge variant={is.variant}>{is.label}</Badge>
            },
        },
        {
            id: 'tx',
            header: '交易哈希',
            cell: ({ row }) => {
                const hash = row.original.tx_hash
                if (!hash) return <span className="text-gray-400">-</span>
                return (
                    <a
                        href={explorerTxUrl(chain, hash)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1"
                        title={hash}
                    >
                        {hash.slice(0, 8)}...{hash.slice(-6)}
                        <ExternalLink className="w-3 h-3" />
                    </a>
                )
            },
        },
        {
            id: 'error',
            header: '错误',
            cell: ({ row }) => {
                const err = row.original.error_message
                if (!err) return <span className="text-gray-400">-</span>
                return (
                    <span className="text-xs text-red-600 dark:text-red-400" title={err}>
                        {row.original.retry_count > 0 && (
                            <span className="text-red-400 mr-1">(重试{row.original.retry_count}次)</span>
                        )}
                        {err.length > 60 ? err.slice(0, 60) + '...' : err}
                    </span>
                )
            },
        },
    ]

    return (
        <div className="w-full flex flex-col gap-6">
            {/* Header */}
            <div className="flex items-center gap-4">
                <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => navigate({ to: '/payouts', search: { tab: 'history' } })}
                >
                    <ArrowLeft className="w-4 h-4" />
                    返回
                </Button>
                <div className="flex-1">
                    <h1 className="text-2xl font-bold text-zinc-900 dark:text-white">
                        批量打款 <span className="font-mono text-lg text-gray-500">#{detail.id}</span>
                    </h1>
                </div>
                <div className="flex items-center gap-2">
                    {failedCount > 0 && (
                        <Button
                            variant="outline"
                            size="sm"
                            className="gap-1.5 text-red-500 border-red-200 dark:border-red-900/30 hover:bg-red-50 dark:hover:bg-red-900/10"
                            disabled={downloading}
                            onClick={() => handleExportCsv('failed')}
                        >
                            <Download className="w-4 h-4" />
                            导出失败记录
                        </Button>
                    )}
                    <Button
                        variant="outline"
                        size="sm"
                        className="gap-1.5"
                        disabled={downloading}
                        onClick={() => handleExportCsv()}
                    >
                        <Download className="w-4 h-4" />
                        导出全部
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={fetchDetail}
                        className="gap-1.5"
                    >
                        <RefreshCw className="w-4 h-4" />
                        刷新
                    </Button>
                </div>
            </div>

            {/* Overview */}
            <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                    <div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">状态</div>
                        <Badge variant={batchStatus.variant} className="text-sm">{batchStatus.label}</Badge>
                    </div>
                    <div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">网络 / 代币</div>
                        <div className="font-semibold text-zinc-900 dark:text-white">{chain} / {tokenLabel}</div>
                    </div>
                    <div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">总金额</div>
                        <div className="font-bold text-lg text-zinc-900 dark:text-white">
                            {formatAmount(detail.total_amount)} <span className="text-sm font-normal text-gray-400">{tokenLabel}</span>
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">笔数</div>
                        <div className="font-semibold text-zinc-900 dark:text-white">{detail.item_count} 笔</div>
                    </div>
                </div>

                <div className="mt-4 pt-4 border-t border-gray-100 dark:border-[#2a2d35] grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">打款钱包: </span>
                        {detail.wallet_address ? (
                            <a
                                href={explorerAddrUrl(chain, detail.wallet_address)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline"
                            >
                                {detail.wallet_address.slice(0, 8)}...{detail.wallet_address.slice(-6)}
                            </a>
                        ) : '-'}
                    </div>
                    {detail.proposal_id && (
                        <div>
                            <span className="text-gray-500 dark:text-gray-400">提案: </span>
                            <span
                                className="text-blue-600 dark:text-blue-400 cursor-pointer hover:underline text-xs"
                                onClick={() => navigate({ to: '/multisig' })}
                            >
                                #{detail.proposal_id}
                            </span>
                        </div>
                    )}
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">创建人: </span>
                        <span className="text-zinc-900 dark:text-white">{detail.created_by_username || '-'}</span>
                    </div>
                    <div>
                        <span className="text-gray-500 dark:text-gray-400">创建时间: </span>
                        <span className="text-zinc-900 dark:text-white text-xs">{formatTime(detail.created_at)}</span>
                    </div>
                    {detail.executed_at && (
                        <div>
                            <span className="text-gray-500 dark:text-gray-400">执行时间: </span>
                            <span className="text-zinc-900 dark:text-white text-xs">{formatTime(detail.executed_at)}</span>
                        </div>
                    )}
                    {detail.memo && (
                        <div className="col-span-2">
                            <span className="text-gray-500 dark:text-gray-400">备注: </span>
                            <span className="text-zinc-900 dark:text-white">{detail.memo}</span>
                        </div>
                    )}
                </div>
            </div>

            {/* Progress bar (when executing) */}
            {['executing', 'pending', 'signing'].includes(detail.status) && detail.items.length > 0 && (
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                    <div className="flex justify-between text-sm mb-2">
                        <span className="text-gray-500 dark:text-gray-400">执行进度</span>
                        <span className="font-medium dark:text-white">
                            {completedCount} / {detail.item_count} 笔完成
                            {failedCount > 0 && <span className="text-red-400 ml-2">({failedCount} 失败)</span>}
                        </span>
                    </div>
                    <div className="w-full h-2 bg-gray-100 dark:bg-[#2a2d35] rounded-full overflow-hidden">
                        <div
                            className="h-full bg-blue-500 rounded-full transition-all duration-500"
                            style={{ width: `${detail.item_count > 0 ? (completedCount / detail.item_count) * 100 : 0}%` }}
                        />
                    </div>
                    <div className="flex gap-4 mt-2 text-xs text-gray-400">
                        <span className="text-emerald-500">✓ 完成 {completedCount}</span>
                        {failedCount > 0 && <span className="text-red-400">✗ 失败 {failedCount}</span>}
                        {pendingCount > 0 && <span>○ 待处理 {pendingCount}</span>}
                    </div>
                </div>
            )}

            {/* Summary badges */}
            {detail.items.length > 0 && (
                <div className="flex gap-3 flex-wrap">
                    <div className="px-3 py-1.5 rounded-lg bg-emerald-50 dark:bg-emerald-900/10 text-emerald-700 dark:text-emerald-400 text-sm font-medium border border-emerald-100 dark:border-emerald-900/30">
                        完成 {completedCount}
                    </div>
                    {failedCount > 0 && (
                        <div className="px-3 py-1.5 rounded-lg bg-red-50 dark:bg-red-900/10 text-red-700 dark:text-red-400 text-sm font-medium border border-red-100 dark:border-red-900/30">
                            失败 {failedCount}
                        </div>
                    )}
                    {pendingCount > 0 && (
                        <div className="px-3 py-1.5 rounded-lg bg-gray-50 dark:bg-[#22252e] text-gray-600 dark:text-gray-400 text-sm font-medium border border-gray-100 dark:border-[#2a2d35]">
                            待处理 {pendingCount}
                        </div>
                    )}
                </div>
            )}

            {/* Detail Table */}
            <div className="elegant-card bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35] overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-100 dark:border-[#2a2d35]">
                    <h2 className="font-semibold text-sm text-gray-700 dark:text-gray-300">打款明细</h2>
                </div>
                {detail.items.length === 0 ? (
                    <div className="text-center py-12 text-gray-400">暂无明细数据</div>
                ) : (
                    <DataTable columns={columns} data={detail.items} />
                )}
            </div>
        </div>
    )
}
