import { createFileRoute, useRouter } from '@tanstack/react-router'
import { Download, Plus, AlertCircle, Fuel, ArrowDownToLine, Send, PenTool, Activity, ShieldCheck, ArrowRight, RefreshCw, Zap } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'
import { useEffect, useState } from 'react'
import { settingsApi, depositApi, proposalApi } from '@/lib/api'

export const Route = createFileRoute('/')({
    component: DashboardComponent,
})

interface WalletBalance {
    id: number
    chain: string
    type: string
    label: string
    usdt_balance: string
    native_balance: string
}

interface Deposit {
    id: number
    chain: string
    token: string
    address: string
    from_address: string
    amount: string
    status: string
    created_at: string
}

function formatBalance(val: string | number) {
    const n = Number(val)
    if (isNaN(n)) return '0.00'
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
    if (n >= 1_000) return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    return n.toFixed(2)
}

function timeAgo(dateStr: string) {
    const diff = Date.now() - new Date(dateStr).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return '刚刚'
    if (mins < 60) return `${mins}分钟前`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `${hours}小时前`
    return `${Math.floor(hours / 24)}天前`
}

function shortenAddr(addr: string) {
    if (!addr) return ''
    if (addr.startsWith('0x')) return addr.slice(0, 6) + '...' + addr.slice(-4)
    return addr.slice(0, 6) + '...' + addr.slice(-4)
}

function DashboardComponent() {
    const router = useRouter()

    const [wallets, setWallets] = useState<WalletBalance[]>([])
    const [deposits, setDeposits] = useState<Deposit[]>([])
    const [pendingCount, setPendingCount] = useState(0)
    const [feeeBalance, setFeeeBalance] = useState<string | null>(null)
    const [feeeEnabled, setFeeeEnabled] = useState(false)
    const [balanceLoading, setBalanceLoading] = useState(true)  // 余额加载状态（慢）
    const [baseLoading, setBaseLoading] = useState(true)        // 充值/待办加载状态（快）

    const fetchData = async () => {
        setBalanceLoading(true)
        setBaseLoading(true)

        // 充值记录和待办提案先加载（快，DB 查询）
        depositApi.list({ page_size: 5 }).then(res => {
            setDeposits(res.data.items || [])
        }).catch(() => {}).finally(() => {})

        proposalApi.list({ status: 'pending', page_size: 1 }).then(res => {
            setPendingCount(res.data.total || 0)
        }).catch(() => {}).finally(() => {
            setBaseLoading(false)
        })

        // feee.io 余额（快，外部 API）
        settingsApi.getFeeeBalance().then(res => {
            setFeeeBalance(res.data.balance)
            setFeeeEnabled(res.data.enabled)
        }).catch(() => {})

        // 余额单独加载（需链上查询，较慢）—— 只查 collection + gas 钱包
        try {
            const walletsRes = await settingsApi.getWalletsWithBalances('collection,gas')
            setWallets(walletsRes.data)
        } catch {
            // 余额加载失败不影响其他数据
        } finally {
            setBalanceLoading(false)
        }
    }

    useEffect(() => { fetchData() }, [])

    // 按链汇总归集钱包余额
    const sumUsdt = (chain: string) =>
        wallets.filter(w => w.chain === chain && w.type === 'collection')
            .reduce((acc, w) => acc + Number(w.usdt_balance), 0)

    const gasBalance = (chain: string) => {
        const gasWallets = wallets.filter(w => w.chain === chain && w.type === 'gas')
        return gasWallets.reduce((acc, w) => acc + Number(w.native_balance), 0)
    }

    const bscUsdt = sumUsdt('BSC')
    const tronUsdt = sumUsdt('TRON')
    const bscGas = gasBalance('BSC')
    const tronGas = gasBalance('TRON')

    const bscGasLow = bscGas < 0.01
    const tronGasLow = tronGas < 10

    return (
        <>
            <div className="flex-1 flex flex-col gap-8 min-w-0">
                {/* Header */}
                <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold text-zinc-900 dark:text-white mb-1">资金总览</h1>
                        <p className="text-sm text-gray-500 dark:text-gray-400">全网实时余额与待处理操作。</p>
                    </div>
                    <div className="flex gap-3">
                        <Button variant="outline" className="gap-2" onClick={fetchData} disabled={balanceLoading || baseLoading}>
                            <RefreshCw className={`w-4 h-4 text-gray-400 dark:text-gray-500 ${balanceLoading || baseLoading ? 'animate-spin' : ''}`} /> 刷新
                        </Button>
                        <Button
                            variant="outline"
                            className="gap-2"
                            onClick={() => toast.info('导出功能开发中', { description: '该功能将在后续版本中上线' })}
                        >
                            <Download className="w-4 h-4 text-gray-400 dark:text-gray-500" /> 导出
                        </Button>
                        <Button className="gap-2" onClick={() => router.navigate({ to: '/payouts', search: { tab: 'batch' } })}>
                            <Plus className="w-4 h-4" /> 新建操作
                        </Button>
                    </div>
                </div>

                {/* Balance Cards */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* BSC */}
                    <Card className="group p-6">
                        <div className="absolute -right-4 -top-4 w-24 h-24 bg-yellow-400/5 dark:bg-yellow-400/10 rounded-full blur-xl group-hover:bg-yellow-400/10 dark:group-hover:bg-yellow-400/20 transition-colors pointer-events-none"></div>
                        <div className="flex justify-between items-center mb-6 relative">
                            <div className="flex items-center gap-2">
                                <div className="w-8 h-8 bg-yellow-50 dark:bg-yellow-500/10 rounded-lg border border-yellow-100 dark:border-yellow-500/20 flex items-center justify-center">
                                    <span className="text-yellow-600 dark:text-yellow-500 text-xs font-bold">BSC</span>
                                </div>
                                <span className="text-sm font-medium text-gray-500 dark:text-gray-400">归集资金池</span>
                            </div>
                            {bscGasLow ? (
                                <div className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10 px-2 py-1 rounded-md border border-amber-100 dark:border-amber-500/20">
                                    <AlertCircle className="w-3.5 h-3.5" /> Gas 不足
                                </div>
                            ) : (
                                <div className="flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 px-2 py-1 rounded-md">
                                    <div className="w-1.5 h-1.5 bg-emerald-500 dark:bg-emerald-400 rounded-full circle-pulse"></div>
                                    运行中
                                </div>
                            )}
                        </div>
                        <div className="relative">
                            <div className="text-[40px] font-semibold leading-none tracking-tight mb-1">
                                {balanceLoading ? (
                                    <span className="text-gray-300 dark:text-gray-600">—</span>
                                ) : (
                                    <>
                                        <span className="balance-amount">{formatBalance(bscUsdt).replace(/\..*/, '')}</span>
                                        <span className="text-[24px] text-gray-400 dark:text-gray-500">.{formatBalance(bscUsdt).split('.')[1] || '00'}</span>
                                    </>
                                )}
                            </div>
                            <div className="text-sm text-gray-400 dark:text-gray-500 font-medium">USDT</div>
                        </div>
                        <div className="mt-6 pt-5 border-t border-gray-100 dark:border-[#2a2d35] flex justify-between items-center">
                            <span className="text-sm text-gray-500 dark:text-gray-400">网络矿工费余额</span>
                            <div className="flex items-center gap-1.5">
                                <Fuel className={`w-3.5 h-3.5 ${bscGasLow ? 'text-amber-500 dark:text-amber-400' : 'text-gray-400 dark:text-gray-500'}`} />
                                <span className={`text-sm font-semibold ${bscGasLow ? 'text-amber-600 dark:text-amber-500' : 'text-zinc-700 dark:text-gray-300'}`}>
                                    {balanceLoading ? '—' : `${bscGas.toFixed(4)} BNB`}
                                </span>
                            </div>
                        </div>
                    </Card>

                    {/* TRON */}
                    <Card className="group p-6">
                        <div className="absolute -right-4 -top-4 w-24 h-24 bg-red-400/5 dark:bg-red-400/10 rounded-full blur-xl group-hover:bg-red-400/10 dark:group-hover:bg-red-400/20 transition-colors pointer-events-none"></div>
                        <div className="flex justify-between items-center mb-6 relative">
                            <div className="flex items-center gap-2">
                                <div className="w-8 h-8 bg-red-50 dark:bg-red-500/10 rounded-lg border border-red-100 dark:border-red-500/20 flex items-center justify-center">
                                    <span className="text-red-600 dark:text-red-500 text-xs font-bold">TRX</span>
                                </div>
                                <span className="text-sm font-medium text-gray-500 dark:text-gray-400">归集资金池</span>
                            </div>
                            {tronGasLow ? (
                                <div className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/10 px-2 py-1 rounded-md border border-amber-100 dark:border-amber-500/20">
                                    <AlertCircle className="w-3.5 h-3.5" /> Gas 不足
                                </div>
                            ) : (
                                <div className="flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 px-2 py-1 rounded-md">
                                    <div className="w-1.5 h-1.5 bg-emerald-500 dark:bg-emerald-400 rounded-full circle-pulse"></div>
                                    运行中
                                </div>
                            )}
                        </div>
                        <div className="relative">
                            <div className="text-[40px] font-semibold leading-none tracking-tight mb-1">
                                {balanceLoading ? (
                                    <span className="text-gray-300 dark:text-gray-600">—</span>
                                ) : (
                                    <>
                                        <span className="balance-amount">{formatBalance(tronUsdt).replace(/\..*/, '')}</span>
                                        <span className="text-[24px] text-gray-400 dark:text-gray-500">.{formatBalance(tronUsdt).split('.')[1] || '00'}</span>
                                    </>
                                )}
                            </div>
                            <div className="text-sm text-gray-400 dark:text-gray-500 font-medium">USDT</div>
                        </div>
                        <div className="mt-6 pt-5 border-t border-gray-100 dark:border-[#2a2d35] flex justify-between items-center">
                            <span className="text-sm text-gray-500 dark:text-gray-400">网络矿工费余额</span>
                            <div className="flex items-center gap-1.5">
                                <Fuel className={`w-3.5 h-3.5 ${tronGasLow ? 'text-amber-500 dark:text-amber-400' : 'text-gray-400 dark:text-gray-500'}`} />
                                <span className={`text-sm font-semibold ${tronGasLow ? 'text-amber-600 dark:text-amber-500' : 'text-zinc-700 dark:text-gray-300'}`}>
                                    {balanceLoading ? '—' : `${tronGas.toFixed(2)} TRX`}
                                </span>
                            </div>
                        </div>
                    </Card>
                </div>

                {/* Feee.io Energy Rental Balance */}
                {feeeEnabled && (
                    <Card className={`p-5 border ${feeeBalance !== null && Number(feeeBalance) < 50 ? 'border-amber-200 dark:border-amber-500/30 bg-gradient-to-br from-amber-50/60 to-white dark:from-amber-900/10 dark:to-[#1c1f26]' : 'border-blue-100 dark:border-blue-500/20 bg-gradient-to-br from-blue-50/40 to-white dark:from-blue-900/10 dark:to-[#1c1f26]'}`}>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${feeeBalance !== null && Number(feeeBalance) < 50 ? 'bg-amber-100 dark:bg-amber-500/20' : 'bg-blue-100 dark:bg-blue-500/20'}`}>
                                    <Zap className={`w-5 h-5 ${feeeBalance !== null && Number(feeeBalance) < 50 ? 'text-amber-600 dark:text-amber-400' : 'text-blue-600 dark:text-blue-400'}`} />
                                </div>
                                <div>
                                    <div className="text-sm font-semibold text-zinc-900 dark:text-white">Feee.io 能量租赁账户</div>
                                    <div className="text-xs text-gray-400 dark:text-gray-500">TRON 能量租赁余额</div>
                                </div>
                            </div>
                            <div className="flex items-center gap-3">
                                {feeeBalance !== null && Number(feeeBalance) < 50 && (
                                    <span className="text-[10px] font-bold uppercase px-2 py-1 rounded-md bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-500/30">
                                        余额不足
                                    </span>
                                )}
                                <div className="text-right">
                                    <span className={`text-2xl font-bold tabular-nums ${feeeBalance !== null && Number(feeeBalance) < 50 ? 'text-amber-600 dark:text-amber-400' : 'text-zinc-900 dark:text-white'}`}>
                                        {feeeBalance !== null ? formatBalance(feeeBalance) : '—'}
                                    </span>
                                    <span className="text-sm text-gray-400 dark:text-gray-500 font-medium ml-1">TRX</span>
                                </div>
                            </div>
                        </div>
                    </Card>
                )}

                {/* Recent Deposits */}
                <Card className="mb-8">
                    <div className="px-6 py-5 border-b border-gray-100 dark:border-[#2a2d35] flex justify-between items-center">
                        <h2 className="text-base font-semibold text-zinc-900 dark:text-white">最近充值记录</h2>
                        <button
                            onClick={() => router.navigate({ to: '/deposits' })}
                            className="text-sm text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 font-medium transition-colors"
                        >
                            查看所有历史
                        </button>
                    </div>

                    <div className="divide-y divide-gray-50 dark:divide-[#2a2d35]">
                        {baseLoading ? (
                            <div className="px-6 py-8 text-center text-sm text-gray-400">加载中...</div>
                        ) : deposits.length === 0 ? (
                            <div className="px-6 py-8 text-center text-sm text-gray-400">暂无充值记录</div>
                        ) : deposits.map(d => (
                            <div
                                key={d.id}
                                className="p-4 hover-row hover:bg-[#f8fafc] dark:hover:bg-[#22252e] px-6 flex items-center justify-between cursor-pointer"
                                onClick={() => router.navigate({ to: '/deposits' })}
                            >
                                <div className="flex items-center gap-4">
                                    <div className="w-10 h-10 rounded-full bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 flex items-center justify-center shrink-0">
                                        <ArrowDownToLine className="w-4 h-4" />
                                    </div>
                                    <div>
                                        <div className="text-sm font-semibold text-zinc-900 dark:text-gray-200 flex flex-wrap items-center gap-2">
                                            收到充值
                                            <span className="px-1.5 py-0.5 rounded text-[10px] uppercase font-bold bg-gray-100 dark:bg-[#2a2d35] text-gray-500 dark:text-gray-400">{d.chain}</span>
                                            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-100 dark:bg-[#2a2d35] text-gray-500 dark:text-gray-400">{d.token}</span>
                                        </div>
                                        <div className="text-xs text-gray-500 dark:text-gray-400 mt-1 font-mono">
                                            来自: {shortenAddr(d.from_address)}
                                        </div>
                                    </div>
                                </div>
                                <div className="text-right">
                                    <div className="text-sm font-semibold text-zinc-900 dark:text-gray-200">
                                        +{Number(d.amount).toFixed(2)} <span className="text-gray-400 dark:text-gray-500 font-normal">{d.token}</span>
                                    </div>
                                    <div className="text-xs text-emerald-600 dark:text-emerald-400 font-medium mt-1">
                                        已确认 • {timeAgo(d.created_at)}
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </Card>
            </div>

            {/* Right Panel */}
            <div className="w-full lg:w-[340px] shrink-0 flex flex-col gap-6">
                <Card className="p-6 bg-gradient-to-b from-white to-blue-50/30 dark:from-[#1c1f26] dark:to-blue-900/10 border-blue-100 dark:border-blue-900/40">
                    <div className="w-12 h-12 bg-blue-100 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400 rounded-2xl flex items-center justify-center mb-4 inner-shadow">
                        <PenTool className="w-6 h-6" />
                    </div>
                    <h3 className="text-lg font-bold text-zinc-900 dark:text-white mb-1">待办事项</h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                        {baseLoading ? '加载中...' : pendingCount > 0
                            ? `您有 ${pendingCount} 个操作正在等待多签审批。`
                            : '暂无待审批操作。'}
                    </p>
                    <Button
                        variant="primary"
                        className="w-full gap-2"
                        onClick={() => router.navigate({ to: '/multisig' })}
                        disabled={pendingCount === 0 && !baseLoading}
                    >
                        审核并签名
                        <ArrowRight className="w-4 h-4" />
                    </Button>
                </Card>

                <Card className="p-6">
                    <h3 className="text-sm font-semibold text-zinc-900 dark:text-white mb-4 uppercase tracking-wider">系统状态</h3>
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                <Activity className="w-4 h-4 text-emerald-500 dark:text-emerald-400" />
                                BSC 归集钱包
                            </div>
                            <span className="text-sm font-medium text-zinc-900 dark:text-gray-200">
                                {wallets.filter(w => w.chain === 'BSC' && w.type === 'collection').length} 个
                            </span>
                        </div>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                <Activity className="w-4 h-4 text-emerald-500 dark:text-emerald-400" />
                                TRON 归集钱包
                            </div>
                            <span className="text-sm font-medium text-zinc-900 dark:text-gray-200">
                                {wallets.filter(w => w.chain === 'TRON' && w.type === 'collection').length} 个
                            </span>
                        </div>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                <ShieldCheck className="w-4 h-4 text-blue-500 dark:text-blue-400" />
                                待审批提案
                            </div>
                            <span className={`text-sm font-medium ${pendingCount > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
                                {baseLoading ? '—' : `${pendingCount} 个`}
                            </span>
                        </div>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                <Send className="w-4 h-4 text-gray-400 dark:text-gray-500" />
                                最近充值
                            </div>
                            <span className="text-sm font-medium text-zinc-900 dark:text-gray-200">
                                {deposits.length > 0 ? timeAgo(deposits[0].created_at) : '—'}
                            </span>
                        </div>
                    </div>
                </Card>
            </div>
        </>
    )
}
