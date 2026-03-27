import { createFileRoute } from '@tanstack/react-router'
import { DataTable } from '@/components/ui/data-table'
import { ColumnDef } from '@tanstack/react-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Search, Filter, ExternalLink, X, ArrowDownCircle, Clock, CheckCircle2, Loader2 } from 'lucide-react'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { toast } from 'sonner'
import { depositApi } from '@/lib/api'
import type { Deposit, DepositStats } from '@/types'

export const Route = createFileRoute('/deposits')({
    component: DepositsComponent,
})

function DepositsComponent() {
    // 数据
    const [deposits, setDeposits] = useState<Deposit[]>([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(20)
    const [, setLoading] = useState(false)
    const [stats, setStats] = useState<DepositStats | null>(null)

    // 筛选
    const [chainFilter, setChainFilter] = useState('')
    const [statusFilter, setStatusFilter] = useState('')
    const [search, setSearch] = useState('')
    const [searchInput, setSearchInput] = useState('')
    const [showFilter, setShowFilter] = useState(false)

    const totalPages = useMemo(() => Math.ceil(total / pageSize), [total, pageSize])

    // ─── 加载数据 ─────────────────────────────────────

    const fetchDeposits = useCallback(async () => {
        setLoading(true)
        try {
            const { data } = await depositApi.list({
                page,
                page_size: pageSize,
                chain: chainFilter || undefined,
                status: statusFilter || undefined,
                search: search || undefined,
            })
            setDeposits(data.items)
            setTotal(data.total)
        } catch {
            toast.error('加载充值记录失败')
        } finally {
            setLoading(false)
        }
    }, [page, pageSize, chainFilter, statusFilter, search])

    const fetchStats = useCallback(async () => {
        try {
            const { data } = await depositApi.stats()
            setStats(data)
        } catch { /* ignore */ }
    }, [])

    useEffect(() => { fetchDeposits() }, [fetchDeposits])
    useEffect(() => { fetchStats() }, [fetchStats])

    // 15 秒自动刷新
    useEffect(() => {
        const timer = setInterval(() => {
            fetchDeposits()
            fetchStats()
        }, 15000)
        return () => clearInterval(timer)
    }, [fetchDeposits, fetchStats])

    // 搜索防抖
    useEffect(() => {
        const timer = setTimeout(() => {
            setSearch(searchInput)
            setPage(1)
        }, 400)
        return () => clearTimeout(timer)
    }, [searchInput])

    // ─── 操作 ──────────────────────────────────────────

    const handleViewTx = (chain: string, txHash: string) => {
        const url = chain === 'BSC'
            ? `https://bscscan.com/tx/${txHash}`
            : `https://tronscan.org/#/transaction/${txHash}`
        window.open(url, '_blank', 'noopener,noreferrer')
    }

    const handlePageSizeChange = (size: number) => {
        setPageSize(size)
        setPage(1)
    }

    const hasActiveFilters = chainFilter !== '' || statusFilter !== ''

    // ─── 列定义 ──────────────────────────────────────

    const columns: ColumnDef<Deposit>[] = [
        {
            accessorKey: 'chain',
            header: '网络',
            cell: ({ row }) => (
                <Badge variant="secondary" className="font-bold uppercase">
                    {row.getValue('chain')}
                </Badge>
            ),
        },
        {
            accessorKey: 'address',
            header: '充值地址',
            cell: ({ row }) => {
                const addr = row.getValue('address') as string
                const short = addr.length > 14 ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : addr
                return <span className="font-mono text-sm text-zinc-900 dark:text-gray-200">{short}</span>
            },
        },
        {
            accessorKey: 'from_address',
            header: '来源地址',
            cell: ({ row }) => {
                const addr = row.original.from_address
                if (!addr) return <span className="text-gray-400">-</span>
                const short = addr.length > 14 ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : addr
                return <span className="font-mono text-sm text-gray-500 dark:text-gray-400">{short}</span>
            },
        },
        {
            accessorKey: 'token',
            header: '代币',
            cell: ({ row }) => {
                const token = row.getValue('token') as string
                const colorMap: Record<string, string> = {
                    'USDT': 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
                    'BNB': 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
                    'TRX': 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
                }
                return (
                    <Badge variant="secondary" className={colorMap[token] || ''}>
                        {token}
                    </Badge>
                )
            },
        },
        {
            accessorKey: 'amount',
            header: '金额',
            cell: ({ row }) => {
                const amount = parseFloat(row.getValue('amount') as string)
                const token = row.original.token || 'USDT'
                const formatted = amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })
                return (
                    <span className={`font-semibold text-[15px] ${amount >= 50000 ? 'text-emerald-600 dark:text-emerald-400' : 'text-zinc-900 dark:text-gray-200'}`}>
                        +{formatted} {token}
                    </span>
                )
            },
        },
        {
            accessorKey: 'status',
            header: '状态',
            cell: ({ row }) => {
                const status = row.getValue('status') as string
                const confirmations = row.original.confirmations
                if (status === 'confirmed') {
                    return <Badge variant="success">已确认</Badge>
                }
                if (status === 'confirming') {
                    return (
                        <Badge variant="default" className="bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                            确认中 ({confirmations})
                        </Badge>
                    )
                }
                return <Badge variant="warning">待确认</Badge>
            },
        },
        {
            accessorKey: 'created_at',
            header: '时间',
            cell: ({ row }) => {
                const dateStr = row.getValue('created_at') as string
                const date = new Date(dateStr)
                const formatted = date.toLocaleString('zh-CN', {
                    month: '2-digit', day: '2-digit',
                    hour: '2-digit', minute: '2-digit',
                })
                return <span className="text-gray-500 dark:text-gray-400 whitespace-nowrap">{formatted}</span>
            },
        },
        {
            id: 'actions',
            header: '操作',
            cell: ({ row }) => (
                <Button
                    variant="ghost"
                    size="sm"
                    className="gap-2 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-500/10"
                    onClick={() => handleViewTx(row.original.chain, row.original.tx_hash)}
                >
                    查看 <ExternalLink className="w-3 h-3" />
                </Button>
            ),
        },
    ]

    // ─── 渲染 ────────────────────────────────────────

    return (
        <div className="w-full flex flex-col gap-6">
            {/* 标题 */}
            <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 dark:text-white mb-1">充值记录监控</h1>
                    <p className="text-sm text-gray-500 dark:text-gray-400">追踪并验证所有支持网络上的入账交易。</p>
                </div>
                <div className="flex items-center gap-3">
                    <div className="relative">
                        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                        <Input
                            className="pl-9 w-[260px]"
                            placeholder="搜索地址或交易哈希..."
                            value={searchInput}
                            onChange={(e) => setSearchInput(e.target.value)}
                        />
                        {searchInput && (
                            <button
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                                onClick={() => setSearchInput('')}
                            >
                                <X className="w-3.5 h-3.5" />
                            </button>
                        )}
                    </div>
                    <div className="relative">
                        <Button
                            variant="outline"
                            className={`gap-2 shrink-0 ${hasActiveFilters ? 'border-blue-300 text-blue-600 dark:border-blue-700 dark:text-blue-400' : ''}`}
                            onClick={() => setShowFilter(!showFilter)}
                        >
                            <Filter className="w-4 h-4" /> 筛选
                            {hasActiveFilters && <span className="w-1.5 h-1.5 bg-blue-500 rounded-full"></span>}
                        </Button>

                        {showFilter && (
                            <div className="absolute right-0 top-full mt-2 w-56 bg-white dark:bg-[#181a20] rounded-xl shadow-lg border border-gray-100 dark:border-[#2a2d35] p-4 animate-slide-down z-30 space-y-4">
                                <div>
                                    <label className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase mb-2 block">网络</label>
                                    <div className="flex gap-2">
                                        {([['', '全部'], ['BSC', 'BSC'], ['TRON', 'TRON']] as const).map(([v, label]) => (
                                            <button
                                                key={v}
                                                onClick={() => { setChainFilter(v); setPage(1) }}
                                                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${chainFilter === v
                                                    ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
                                                    : 'bg-gray-100 dark:bg-[#2a2d35] text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-[#3a3e47]'
                                                    }`}
                                            >
                                                {label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <div>
                                    <label className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase mb-2 block">状态</label>
                                    <div className="flex gap-2 flex-wrap">
                                        {([['', '全部'], ['confirmed', '已确认'], ['confirming', '确认中'], ['pending', '待确认']] as const).map(([v, label]) => (
                                            <button
                                                key={v}
                                                onClick={() => { setStatusFilter(v); setPage(1) }}
                                                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${statusFilter === v
                                                    ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
                                                    : 'bg-gray-100 dark:bg-[#2a2d35] text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-[#3a3e47]'
                                                    }`}
                                            >
                                                {label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                {hasActiveFilters && (
                                    <button
                                        onClick={() => { setChainFilter(''); setStatusFilter(''); setPage(1) }}
                                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                                    >
                                        清除所有筛选
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* 统计卡片 */}
            {stats && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    <StatCard
                        icon={<ArrowDownCircle className="w-5 h-5 text-blue-500" />}
                        label="今日充值"
                        value={`${stats.total_today} 笔`}
                        sub={stats.amount_by_token?.length > 0
                            ? stats.amount_by_token.map(t => `${parseFloat(t.amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${t.token}`).join(' / ')
                            : `${parseFloat(stats.amount_today).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                        }
                    />
                    <StatCard
                        icon={<Clock className="w-5 h-5 text-amber-500" />}
                        label="待确认"
                        value={`${stats.pending_count}`}
                    />
                    <StatCard
                        icon={<Loader2 className="w-5 h-5 text-blue-500" />}
                        label="确认中"
                        value={`${stats.confirming_count}`}
                    />
                    <StatCard
                        icon={<CheckCircle2 className="w-5 h-5 text-emerald-500" />}
                        label="今日已确认"
                        value={`${stats.confirmed_today}`}
                    />
                </div>
            )}

            {/* 数据表 */}
            <DataTable
                columns={columns}
                data={deposits}
                page={page}
                totalPages={totalPages}
                total={total}
                onPageChange={setPage}
                pageSize={pageSize}
                onPageSizeChange={handlePageSizeChange}
            />
        </div>
    )
}

// ─── 统计卡片组件 ──────────────────────────────────────

function StatCard({ icon, label, value, sub }: {
    icon: React.ReactNode
    label: string
    value: string
    sub?: string
}) {
    return (
        <div className="bg-white dark:bg-[#181a20] rounded-xl border border-gray-100 dark:border-[#2a2d35] p-4 flex items-start gap-3">
            <div className="mt-0.5">{icon}</div>
            <div>
                <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">{label}</div>
                <div className="text-lg font-bold text-zinc-900 dark:text-white">{value}</div>
                {sub && <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{sub}</div>}
            </div>
        </div>
    )
}
