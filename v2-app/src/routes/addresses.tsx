import { createFileRoute } from '@tanstack/react-router'
import { DataTable } from '@/components/ui/data-table'
import { ColumnDef } from '@tanstack/react-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Modal } from '@/components/ui/modal'
import { Select } from '@/components/ui/select'
import { Search, Plus, Copy, QrCode, X, CheckCircle2, Pencil, Loader2, AlertTriangle, Wallet } from 'lucide-react'
import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import { QRCodeSVG } from 'qrcode.react'
import { addressApi } from '@/lib/api'

// ─── Types ──────────────────────────────────────────

type AddressItem = {
    id: number
    chain: string
    derive_index: number
    address: string
    label: string | null
    is_active: boolean
    created_at: string
}

export const Route = createFileRoute('/addresses')({
    component: AddressesComponent,
})

function AddressesComponent() {
    // 列表数据
    const [addresses, setAddresses] = useState<AddressItem[]>([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(20)
    const [loading, setLoading] = useState(false)

    // 筛选
    const [chainFilter, setChainFilter] = useState('')
    const [search, setSearch] = useState('')
    const [searchInput, setSearchInput] = useState('')

    // 生成弹窗
    const [genModalOpen, setGenModalOpen] = useState(false)
    const [genChain, setGenChain] = useState('BSC')
    const [genCount, setGenCount] = useState(1)
    const [genLabel, setGenLabel] = useState('')
    const [isGenerating, setIsGenerating] = useState(false)

    // 二维码弹窗
    const [qrModalOpen, setQrModalOpen] = useState(false)
    const [qrAddress, setQrAddress] = useState('')
    const [qrChain, setQrChain] = useState('')

    // 编辑备注弹窗
    const [editModalOpen, setEditModalOpen] = useState(false)
    const [editingAddr, setEditingAddr] = useState<AddressItem | null>(null)
    const [editLabel, setEditLabel] = useState('')
    const [isSavingLabel, setIsSavingLabel] = useState(false)

    // 余额查询
    const [balanceMap, setBalanceMap] = useState<Record<number, { native_symbol: string; native_balance: string | null; usdt_balance: string | null } | 'loading' | 'error'>>({})

    const handleQueryBalance = async (addr: AddressItem) => {
        if (balanceMap[addr.id] === 'loading') return
        setBalanceMap(prev => ({ ...prev, [addr.id]: 'loading' }))
        try {
            const { data } = await addressApi.balance(addr.id)
            setBalanceMap(prev => ({ ...prev, [addr.id]: data }))
        } catch {
            setBalanceMap(prev => ({ ...prev, [addr.id]: 'error' }))
            toast.error('查询余额失败')
        }
    }

    // HD 钱包状态
    const [walletStatus, setWalletStatus] = useState<{
        mnemonic_configured: boolean
        total_addresses: number
        bsc_count: number
        tron_count: number
    } | null>(null)

    // ─── 加载状态 ─────────────────────────────────────

    const fetchStatus = useCallback(async () => {
        try {
            const { data } = await addressApi.status()
            setWalletStatus(data)
        } catch { /* ignore */ }
    }, [])

    useEffect(() => {
        fetchStatus()
    }, [fetchStatus])

    // ─── 加载数据 ─────────────────────────────────────

    const fetchAddresses = useCallback(async () => {
        setLoading(true)
        try {
            const { data } = await addressApi.list({
                page,
                page_size: pageSize,
                chain: chainFilter || undefined,
                search: search || undefined,
            })
            setAddresses(data.items)
            setTotal(data.total)
        } catch {
            toast.error('加载地址列表失败')
        } finally {
            setLoading(false)
        }
    }, [page, pageSize, chainFilter, search])

    useEffect(() => {
        fetchAddresses()
    }, [fetchAddresses])

    // 搜索防抖
    useEffect(() => {
        const timer = setTimeout(() => {
            setSearch(searchInput)
            setPage(1)
        }, 400)
        return () => clearTimeout(timer)
    }, [searchInput])

    // ─── 操作 ──────────────────────────────────────────

    const handleCopy = (address: string) => {
        navigator.clipboard.writeText(address).then(() => {
            toast.success('已复制到剪贴板', { description: address })
        }).catch(() => {
            toast.error('复制失败')
        })
    }

    const handleShowQR = (addr: AddressItem) => {
        setQrAddress(addr.address)
        setQrChain(addr.chain)
        setQrModalOpen(true)
    }

    const handleEditLabel = (addr: AddressItem) => {
        setEditingAddr(addr)
        setEditLabel(addr.label || '')
        setEditModalOpen(true)
    }

    const handleSaveLabel = async () => {
        if (!editingAddr) return
        setIsSavingLabel(true)
        try {
            await addressApi.update(editingAddr.id, { label: editLabel || null })
            toast.success('备注已更新')
            setEditModalOpen(false)
            fetchAddresses()
        } catch {
            toast.error('更新备注失败')
        } finally {
            setIsSavingLabel(false)
        }
    }

    const handleGenerate = async () => {
        if (genCount < 1 || genCount > 100) {
            toast.error('数量范围为 1-100')
            return
        }
        setIsGenerating(true)
        try {
            const { data } = await addressApi.generate({
                chain: genChain,
                count: genCount,
                label: genLabel || undefined,
            })
            toast.success(`成功生成 ${data.generated} 个 ${genChain} 地址`)
            setGenModalOpen(false)
            setGenLabel('')
            setGenCount(1)
            setPage(1)
            fetchAddresses()
            fetchStatus()
        } catch (err: unknown) {
            const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '生成失败'
            toast.error(msg)
        } finally {
            setIsGenerating(false)
        }
    }

    const handleChainFilter = (val: string) => {
        setChainFilter(val)
        setPage(1)
    }

    const handlePageSizeChange = (size: number) => {
        setPageSize(size)
        setPage(1)
    }

    // ─── 统计 ──────────────────────────────────────────

    const totalPages = Math.ceil(total / pageSize)

    // ─── 表格列 ─────────────────────────────────────────

    const columns: ColumnDef<AddressItem>[] = [
        {
            accessorKey: 'chain',
            header: '网络',
            cell: ({ row }) => {
                const chain = row.getValue('chain') as string
                return (
                    <Badge variant="secondary" className={`font-bold uppercase text-[10px] ${chain === 'BSC' ? 'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-400' : 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400'}`}>
                        {chain}
                    </Badge>
                )
            },
        },
        {
            accessorKey: 'address',
            header: '地址',
            cell: ({ row }) => {
                const addr = row.getValue('address') as string
                const short = addr.length > 16 ? `${addr.slice(0, 8)}...${addr.slice(-6)}` : addr
                return (
                    <span className="font-mono text-sm text-zinc-900 dark:text-gray-200 cursor-pointer hover:text-blue-600 dark:hover:text-blue-400 transition-colors" title={addr} onClick={() => handleCopy(addr)}>
                        {short}
                    </span>
                )
            },
        },
        {
            accessorKey: 'label',
            header: '备注',
            cell: ({ row }) => {
                const label = row.getValue('label') as string | null
                return (
                    <span className="text-gray-600 dark:text-gray-400 font-medium">
                        {label || <span className="text-gray-300 dark:text-gray-600">-</span>}
                    </span>
                )
            },
        },
        {
            accessorKey: 'derive_index',
            header: '派生索引',
            cell: ({ row }) => (
                <span className="text-gray-500 font-mono text-xs">#{row.getValue('derive_index') as number}</span>
            ),
        },
        {
            accessorKey: 'created_at',
            header: '创建时间',
            cell: ({ row }) => {
                const dt = new Date(row.getValue('created_at') as string)
                return <span className="text-gray-500 whitespace-nowrap text-xs">{dt.toLocaleString('zh-CN')}</span>
            },
        },
        {
            id: 'balance',
            header: '余额',
            cell: ({ row }) => {
                const bal = balanceMap[row.original.id]
                if (!bal) {
                    return (
                        <Button variant="ghost" size="sm" className="text-xs gap-1 text-blue-600 dark:text-blue-400 hover:text-blue-700 px-2 h-7" onClick={() => handleQueryBalance(row.original)}>
                            <Wallet className="w-3.5 h-3.5" /> 查询
                        </Button>
                    )
                }
                if (bal === 'loading') {
                    return <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                }
                if (bal === 'error') {
                    return (
                        <Button variant="ghost" size="sm" className="text-xs text-red-500 hover:text-red-600 px-2 h-7" onClick={() => handleQueryBalance(row.original)}>
                            重试
                        </Button>
                    )
                }
                return (
                    <div className="flex flex-col gap-0.5 text-xs font-mono cursor-pointer" title="点击刷新" onClick={() => handleQueryBalance(row.original)}>
                        <span className="text-zinc-700 dark:text-gray-300">{bal.native_balance ?? '-'} <span className="text-[10px] text-gray-400">{bal.native_symbol}</span></span>
                        <span className="text-zinc-700 dark:text-gray-300">{bal.usdt_balance ?? '-'} <span className="text-[10px] text-gray-400">USDT</span></span>
                    </div>
                )
            },
        },
        {
            id: 'actions',
            header: '操作',
            cell: ({ row }) => (
                <div className="flex items-center gap-1">
                    <Button variant="ghost" size="icon" title="复制地址" onClick={() => handleCopy(row.original.address)}>
                        <Copy className="w-4 h-4 text-gray-400" />
                    </Button>
                    <Button variant="ghost" size="icon" title="二维码" onClick={() => handleShowQR(row.original)}>
                        <QrCode className="w-4 h-4 text-gray-400" />
                    </Button>
                    <Button variant="ghost" size="icon" title="编辑备注" onClick={() => handleEditLabel(row.original)}>
                        <Pencil className="w-4 h-4 text-gray-400" />
                    </Button>
                </div>
            ),
        },
    ]

    return (
        <div className="w-full flex flex-col gap-6">
            {/* 助记词未配置警告 */}
            {walletStatus && !walletStatus.mnemonic_configured && (
                <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800/30 text-amber-700 dark:text-amber-400 text-sm">
                    <AlertTriangle className="w-5 h-5 shrink-0" />
                    <span>HD 助记词未配置，无法生成地址。请在服务器 <code className="px-1.5 py-0.5 bg-amber-100 dark:bg-amber-900/30 rounded text-xs font-mono">.env</code> 文件中设置 <code className="px-1.5 py-0.5 bg-amber-100 dark:bg-amber-900/30 rounded text-xs font-mono">HD_MNEMONIC</code> 后重启后端。</span>
                </div>
            )}

            {/* Header */}
            <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 dark:text-white mb-1">地址库管理</h1>
                    <p className="text-sm text-gray-500 dark:text-gray-400">
                        管理 HD 派生充值地址
                        {walletStatus && (
                            <span className="ml-2">
                                — 共 {walletStatus.total_addresses} 个
                                {walletStatus.bsc_count > 0 && <span className="ml-1.5">BSC {walletStatus.bsc_count}</span>}
                                {walletStatus.tron_count > 0 && <span className="ml-1.5">TRON {walletStatus.tron_count}</span>}
                            </span>
                        )}
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    <Select
                        value={chainFilter}
                        onChange={handleChainFilter}
                        options={[
                            { value: '', label: '全部网络' },
                            { value: 'BSC', label: 'BSC' },
                            { value: 'TRON', label: 'TRON' },
                        ]}
                        className="w-[130px]"
                    />
                    <div className="relative">
                        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                        <Input
                            className="pl-9 w-[220px]"
                            placeholder="搜索地址或备注..."
                            value={searchInput}
                            onChange={(e) => setSearchInput(e.target.value)}
                        />
                        {searchInput && (
                            <button
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                                onClick={() => { setSearchInput(''); setSearch(''); setPage(1) }}
                            >
                                <X className="w-3.5 h-3.5" />
                            </button>
                        )}
                    </div>
                    <Button variant="primary" className="gap-2 shrink-0" onClick={() => setGenModalOpen(true)} disabled={walletStatus !== null && !walletStatus.mnemonic_configured}>
                        <Plus className="w-4 h-4" /> 生成新地址
                    </Button>
                </div>
            </div>

            {/* Table */}
            {loading ? (
                <div className="flex items-center justify-center py-20">
                    <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                </div>
            ) : (
                <DataTable
                    columns={columns}
                    data={addresses}
                    page={page}
                    totalPages={totalPages}
                    total={total}
                    onPageChange={setPage}
                    pageSize={pageSize}
                    onPageSizeChange={handlePageSizeChange}
                />
            )}

            {/* Generate Modal */}
            <Modal isOpen={genModalOpen} onClose={() => setGenModalOpen(false)} title="生成新地址">
                <div className="flex flex-col gap-5">
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">网络</label>
                        <Select
                            value={genChain}
                            onChange={(val) => setGenChain(val)}
                            options={[
                                { value: 'BSC', label: 'BSC (BNB Smart Chain)' },
                                { value: 'TRON', label: 'TRON' },
                            ]}
                        />
                    </div>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">数量</label>
                        <Input
                            type="number"
                            value={genCount}
                            onChange={(e) => setGenCount(Math.max(1, Math.min(100, parseInt(e.target.value) || 1)))}
                            min={1}
                            max={100}
                            className="h-11"
                        />
                    </div>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">备注 (可选)</label>
                        <Input
                            placeholder="例如：VIP 用户池"
                            className="h-11"
                            value={genLabel}
                            onChange={(e) => setGenLabel(e.target.value)}
                        />
                    </div>
                    <Button
                        variant="primary"
                        size="lg"
                        className="w-full mt-4 gap-2"
                        onClick={handleGenerate}
                        disabled={isGenerating}
                    >
                        {isGenerating ? (
                            <><Loader2 className="w-4 h-4 animate-spin" /> 正在生成...</>
                        ) : (
                            <><CheckCircle2 className="w-4 h-4" /> 确认生成</>
                        )}
                    </Button>
                </div>
            </Modal>

            {/* QR Code Modal */}
            <Modal isOpen={qrModalOpen} onClose={() => setQrModalOpen(false)} title="地址二维码">
                <div className="flex flex-col items-center gap-5">
                    <div className="p-4 bg-white rounded-2xl">
                        <QRCodeSVG value={qrAddress} size={192} />
                    </div>
                    <Badge variant="secondary" className="font-bold uppercase text-xs">
                        {qrChain}
                    </Badge>
                    <div className="w-full bg-gray-50 dark:bg-[#1c1f26] p-4 rounded-xl">
                        <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">完整地址</div>
                        <div className="font-mono text-sm text-zinc-900 dark:text-gray-200 break-all">{qrAddress}</div>
                    </div>
                    <Button
                        variant="primary"
                        className="w-full gap-2"
                        onClick={() => { handleCopy(qrAddress); setQrModalOpen(false) }}
                    >
                        <Copy className="w-4 h-4" /> 复制地址
                    </Button>
                </div>
            </Modal>

            {/* Edit Label Modal */}
            <Modal isOpen={editModalOpen} onClose={() => setEditModalOpen(false)} title="编辑备注">
                <div className="flex flex-col gap-4">
                    <div className="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-[#1c1f26] p-3 rounded-xl font-mono break-all">
                        {editingAddr?.address}
                    </div>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">备注</label>
                        <Input
                            placeholder="输入备注标签"
                            className="h-11"
                            value={editLabel}
                            onChange={(e) => setEditLabel(e.target.value)}
                            maxLength={200}
                        />
                    </div>
                    <Button
                        variant="primary"
                        size="lg"
                        className="w-full mt-2 gap-2"
                        onClick={handleSaveLabel}
                        disabled={isSavingLabel}
                    >
                        {isSavingLabel ? (
                            <><Loader2 className="w-4 h-4 animate-spin" /> 保存中...</>
                        ) : (
                            '保存'
                        )}
                    </Button>
                </div>
            </Modal>
        </div>
    )
}
