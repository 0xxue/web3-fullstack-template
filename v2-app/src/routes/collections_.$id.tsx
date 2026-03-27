import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState, useEffect, useCallback, useRef } from 'react'
import { DataTable } from '@/components/ui/data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ColumnDef } from '@tanstack/react-table'
import { ArrowLeft, ExternalLink, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { collectionApi } from '@/lib/api'

// ─── Types ───────────────────────────────────────────

type CollectionDetailItem = {
    id: number
    address: string
    amount: string
    tx_hash: string | null
    gas_tx_hash: string | null
    status: string
    error_message: string | null
    retry_count: number
    created_at: string
    updated_at: string
}

type CollectionDetail = {
    id: number
    chain: string
    status: string
    total_amount: string
    address_count: number
    asset_type?: string
    target_address?: string
    created_by: number
    executed_at: string | null
    created_at: string
    updated_at: string
    items: CollectionDetailItem[]
}

// ─── Helpers ─────────────────────────────────────────

function formatAmount(val: number | string) {
    const num = typeof val === 'string' ? parseFloat(val) : val
    if (isNaN(num)) return '0.00'
    return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
}

function formatTime(iso: string) {
    try {
        const d = new Date(iso)
        return d.toLocaleString('zh-CN', {
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

const statusMap: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
    executing: { label: '执行中', variant: 'warning' },
    completed: { label: '已完成', variant: 'success' },
    partial: { label: '部分完成', variant: 'warning' },
    failed: { label: '已失败', variant: 'destructive' },
    pending: { label: '待签名', variant: 'secondary' },
    signing: { label: '签名中', variant: 'warning' },
    cancelled: { label: '已取消', variant: 'secondary' },
}

const itemStatusMap: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
    pending: { label: '待处理', variant: 'secondary' },
    gas_sent: { label: '已补Gas', variant: 'warning' },
    transferring: { label: '转账中', variant: 'warning' },
    completed: { label: '已完成', variant: 'success' },
    failed: { label: '失败', variant: 'destructive' },
}

// ─── Route ───────────────────────────────────────────

export const Route = createFileRoute('/collections_/$id')({
    component: CollectionDetailPage,
})

function CollectionDetailPage() {
    const { id } = Route.useParams()
    const navigate = useNavigate()
    const [detail, setDetail] = useState<CollectionDetail | null>(null)
    const [loading, setLoading] = useState(true)

    const fetchDetail = useCallback(async () => {
        try {
            const { data } = await collectionApi.getDetail(Number(id))
            setDetail(data)
        } catch {
            toast.error('加载归集详情失败')
        } finally {
            setLoading(false)
        }
    }, [id])

    useEffect(() => { fetchDetail() }, [fetchDetail])

    // 执行中自动刷新（非终态时每 3 秒刷新）
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
                归集记录不存在
                <Button variant="outline" className="ml-4" onClick={() => navigate({ to: '/collections' })}>
                    返回列表
                </Button>
            </div>
        )
    }

    const chain = detail.chain
    const assetType = detail.asset_type || 'usdt'
    const tokenLabel = assetType === 'native' ? (chain === 'BSC' ? 'BNB' : 'TRX') : 'USDT'
    const s = statusMap[detail.status] || { label: detail.status, variant: 'secondary' as const }

    // 统计
    const completedCount = detail.items.filter(i => i.status === 'completed').length
    const failedCount = detail.items.filter(i => i.status === 'failed').length
    const pendingCount = detail.items.filter(i => !['completed', 'failed'].includes(i.status)).length

    const columns: ColumnDef<CollectionDetailItem>[] = [
        {
            accessorKey: 'address',
            header: '地址',
            cell: ({ row }) => {
                const addr = row.original.address
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
            accessorKey: 'status',
            header: '状态',
            cell: ({ row }) => {
                const is = itemStatusMap[row.original.status] || itemStatusMap.pending
                return <Badge variant={is.variant}>{is.label}</Badge>
            },
        },
        {
            id: 'gas_tx',
            header: 'Gas TX',
            cell: ({ row }) => {
                const hash = row.original.gas_tx_hash
                if (!hash) return <span className="text-gray-400">-</span>
                return (
                    <a
                        href={explorerTxUrl(chain, hash)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-mono text-xs text-amber-600 dark:text-amber-400 hover:underline inline-flex items-center gap-1"
                        title={hash}
                    >
                        {hash.slice(0, 8)}...{hash.slice(-6)}
                        <ExternalLink className="w-3 h-3" />
                    </a>
                )
            },
        },
        {
            id: 'tx',
            header: '转账 TX',
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
            header: '错误信息',
            cell: ({ row }) => {
                const err = row.original.error_message
                if (!err) return <span className="text-gray-400">-</span>
                return (
                    <span className="text-xs text-red-600 dark:text-red-400" title={err}>
                        {row.original.retry_count > 0 && <span className="text-red-400 mr-1">(重试{row.original.retry_count}次)</span>}
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
                    onClick={() => navigate({ to: '/collections' })}
                >
                    <ArrowLeft className="w-4 h-4" />
                    返回
                </Button>
                <div className="flex-1">
                    <h1 className="text-2xl font-bold text-zinc-900 dark:text-white">
                        归集详情 <span className="font-mono text-lg text-gray-500">#{detail.id}</span>
                    </h1>
                </div>
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

            {/* Target Address */}
            {detail.target_address && (
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35] flex items-center justify-between">
                    <span className="text-sm text-gray-500">归集目标钱包</span>
                    <a
                        href={explorerAddrUrl(chain, detail.target_address)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-mono text-sm text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1"
                    >
                        {detail.target_address}
                        <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                </div>
            )}

            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                    <div className="text-xs text-gray-500 mb-1">网络</div>
                    <Badge variant="secondary" className="font-bold uppercase">{chain}</Badge>
                </div>
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                    <div className="text-xs text-gray-500 mb-1">状态</div>
                    <Badge variant={s.variant}>{s.label}</Badge>
                </div>
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                    <div className="text-xs text-gray-500 mb-1">归集总额</div>
                    <div className="font-bold text-emerald-600 dark:text-emerald-400">
                        {formatAmount(detail.total_amount)} <span className="text-xs font-normal">{tokenLabel}</span>
                    </div>
                </div>
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                    <div className="text-xs text-gray-500 mb-1">地址数</div>
                    <div className="font-bold text-zinc-900 dark:text-white">{detail.address_count}</div>
                </div>
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                    <div className="text-xs text-gray-500 mb-1">创建时间</div>
                    <div className="text-sm text-gray-700 dark:text-gray-300">{formatTime(detail.created_at)}</div>
                </div>
                {detail.executed_at && (
                    <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                        <div className="text-xs text-gray-500 mb-1">完成时间</div>
                        <div className="text-sm text-gray-700 dark:text-gray-300">{formatTime(detail.executed_at)}</div>
                    </div>
                )}
            </div>

            {/* Progress Bar */}
            {detail.items.length > 0 && (
                <div className="elegant-card p-4 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">执行进度</span>
                        <span className="text-sm text-gray-500">
                            {completedCount}/{detail.items.length} 完成
                            {failedCount > 0 && <span className="text-red-500 ml-2">{failedCount} 失败</span>}
                            {pendingCount > 0 && <span className="text-gray-400 ml-2">{pendingCount} 待处理</span>}
                        </span>
                    </div>
                    <div className="w-full h-2 bg-gray-100 dark:bg-[#2a2d35] rounded-full overflow-hidden">
                        <div className="h-full flex">
                            <div
                                className="bg-emerald-500 transition-all duration-500"
                                style={{ width: `${(completedCount / detail.items.length) * 100}%` }}
                            />
                            <div
                                className="bg-red-500 transition-all duration-500"
                                style={{ width: `${(failedCount / detail.items.length) * 100}%` }}
                            />
                        </div>
                    </div>
                </div>
            )}

            {/* Items Table */}
            <div>
                <h2 className="text-lg font-semibold text-zinc-900 dark:text-white mb-4">
                    转账明细 <span className="text-sm font-normal text-gray-500">共 {detail.items.length} 条</span>
                </h2>
                <DataTable columns={columns} data={detail.items} />
            </div>
        </div>
    )
}
