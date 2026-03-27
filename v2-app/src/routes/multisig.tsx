import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Modal } from '@/components/ui/modal'
import {
    PenTool, ShieldCheck, ArrowRight, Loader2, CheckCircle2,
    AlertTriangle, Search, ChevronLeft, ChevronRight, X,
    Plus, Ban, Wallet,
} from 'lucide-react'
import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import { useAccount, useSignTypedData, useSignMessage, useDisconnect } from 'wagmi'
import { useAppKit } from '@reown/appkit/react'
import { proposalApi } from '@/lib/api'
import {
    requestTronAccess, getTronAddress, signTronMessage, signTronTransaction,
    detectTronWallets, setSelectedTronWallet, getSelectedTronWallet,
    type TronWalletId,
} from '@/lib/tron'
import type { MultisigProposal } from '@/types'

export const Route = createFileRoute('/multisig')({
    component: MultisigComponent,
})

const PAGE_SIZE = 4

function MultisigComponent() {
    // ─── 列表状态 ─────────────────────────────────
    const [proposals, setProposals] = useState<MultisigProposal[]>([])
    const [total, setTotal] = useState(0)
    const [loading, setLoading] = useState(true)
    const [statusFilter, setStatusFilter] = useState<string>('all')
    const [search, setSearch] = useState('')
    const [page, setPage] = useState(1)

    // ─── 签名弹窗 ─────────────────────────────────
    const [signModalOpen, setSignModalOpen] = useState(false)
    const [signingProposal, setSigningProposal] = useState<MultisigProposal | null>(null)
    const [isSigning, setIsSigning] = useState(false)
    const [signSuccess, setSignSuccess] = useState(false)
    const [signResult, setSignResult] = useState<{ auto_executed: boolean; execution_tx_hash: string | null } | null>(null)
    // 正在后台执行的提案 ID（TRON 异步任务）
    const [executingIds, setExecutingIds] = useState<Set<number>>(new Set())

    // ─── 创建提案下拉 ─────────────────────────────
    const [createDropdownOpen, setCreateDropdownOpen] = useState(false)
    const navigate = useNavigate()

    // ─── TRON 钱包状态 ────────────────────────────
    const [tronAddress, setTronAddress] = useState<string | null>(null)  // 扩展注入
    const [tronConnecting, setTronConnecting] = useState(false)
    const [tronWalletPickerOpen, setTronWalletPickerOpen] = useState(false)
    const [tronWalletId, setTronWalletId] = useState<TronWalletId>(getSelectedTronWallet())

    const effectiveTronAddress = tronAddress

    // 监听 TRON 钱包账号切换（扩展注入）
    useEffect(() => {
        const checkInterval = setInterval(() => {
            const tw = window.tronWeb
            const currentAddr = tw?.defaultAddress?.base58 || null
            setTronAddress(prev => {
                // 只做地址切换检测，不自动断开（避免钱包短暂刷新时误断）
                if (prev && currentAddr && prev !== currentAddr) return currentAddr
                return prev
            })
        }, 2000)
        return () => clearInterval(checkInterval)
    }, [])

    // ─── BSC 钱包 (wagmi) ─────────────────────────
    const { address: bscAddress, isConnected: bscConnected } = useAccount()
    const { signTypedDataAsync } = useSignTypedData()
    const { signMessageAsync } = useSignMessage()
    const { open: openAppKit } = useAppKit()
    const { disconnect: disconnectBsc } = useDisconnect()

    // ─── 加载提案列表 ─────────────────────────────
    const fetchProposals = useCallback(async () => {
        setLoading(true)
        try {
            const params: Record<string, unknown> = { page, page_size: PAGE_SIZE }
            if (statusFilter !== 'all') params.status = statusFilter
            const { data } = await proposalApi.list(params)
            setProposals(data.items)
            setTotal(data.total)
            // 恢复执行中状态：status=executing 的提案自动加入轮询
            const inFlight = data.items
                .filter((p: import('@/types').MultisigProposal) =>
                    p.status === 'executing' ||
                    (p.status === 'signing' && p.current_signatures >= p.threshold && p.chain === 'TRON')
                )
                .map((p: import('@/types').MultisigProposal) => p.id)
            if (inFlight.length > 0) {
                setExecutingIds(prev => {
                    const next = new Set(prev)
                    inFlight.forEach((id: number) => next.add(id))
                    return next
                })
            }
        } catch (err) {
            const status = (err as { response?: { status?: number } })?.response?.status
            // 401/403 = 未登录，由拦截器处理跳转，不显示错误
            // 无 token = 同上，静默失败
            const hasToken = !!localStorage.getItem('vault-auth-storage')
            if (status !== 401 && status !== 403 && hasToken) {
                toast.error('加载提案列表失败')
            }
        } finally {
            setLoading(false)
        }
    }, [page, statusFilter])

    useEffect(() => { fetchProposals() }, [fetchProposals])

    // 轮询后台执行中的提案，直到状态变为终态
    useEffect(() => {
        if (executingIds.size === 0) return
        const timer = setInterval(async () => {
            const ids = Array.from(executingIds)
            const updates: Record<number, MultisigProposal> = {}
            const completed = new Set<number>()
            await Promise.allSettled(ids.map(async (id) => {
                try {
                    const { data } = await proposalApi.getDetail(id)
                    updates[id] = data
                    if (['executed', 'rejected', 'failed'].includes(data.status)) {
                        completed.add(id)
                        if (data.status === 'executed') {
                            toast.success(`提案 #${id} 执行成功`, {
                                description: data.execution_tx_hash
                                    ? `Tx: ${data.execution_tx_hash.slice(0, 16)}...`
                                    : undefined,
                            })
                        } else if (data.status === 'failed') {
                            toast.error(`提案 #${id} 执行失败`)
                        } else {
                            toast.info(`提案 #${id} 已取消`)
                        }
                    }
                } catch { /* ignore */ }
            }))
            if (Object.keys(updates).length > 0) {
                setProposals(prev => prev.map(p => updates[p.id] ?? p))
            }
            if (completed.size > 0) {
                setExecutingIds(prev => {
                    const next = new Set(prev)
                    completed.forEach(id => next.delete(id))
                    return next
                })
            }
        }, 4000)
        return () => clearInterval(timer)
    }, [executingIds])

    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

    // ─── 筛选搜索 ─────────────────────────────────
    const displayProposals = search
        ? proposals.filter(p => {
            const q = search.toLowerCase()
            const memo = p.tx_data ? String((p.tx_data as Record<string, unknown>)._memo || '') : ''
            return p.title?.toLowerCase().includes(q) ||
                p.to_address?.toLowerCase().includes(q) ||
                memo.toLowerCase().includes(q)
        })
        : proposals

    const handleFilterChange = (filter: string) => {
        setStatusFilter(filter)
        setPage(1)
    }

    // ─── 打开签名弹窗 ────────────────────────────
    // ─── 连接 TRON 钱包 ──────────────────────────
    const handleConnectTronWithWallet = async (walletId: TronWalletId) => {
        const names: Record<TronWalletId, string> = {
            tronlink: 'TronLink', okx: 'OKX Wallet', tp: 'TokenPocket', auto: '钱包',
        }
        // 未安装的钱包直接报错
        const wallets = detectTronWallets()
        const wallet = wallets.find(w => w.id === walletId)
        if (!wallet?.available) {
            toast.error(`未检测到 ${names[walletId]}，请安装后刷新页面重试`)
            setTronWalletPickerOpen(false)
            return
        }
        setSelectedTronWallet(walletId)
        setTronWalletId(walletId)
        setTronWalletPickerOpen(false)
        setTronConnecting(true)
        try {
            const addr = await requestTronAccess()
            if (addr) {
                setTronAddress(addr)
                toast.success(`${names[walletId]} 已连接: ${addr.slice(0, 6)}...${addr.slice(-4)}`)
            } else {
                toast.error(`连接失败，请确认 ${names[walletId]} 已解锁`)
            }
        } catch {
            toast.error('连接 TRON 钱包失败')
        } finally {
            setTronConnecting(false)
        }
    }

    const handleOpenSign = async (proposal: MultisigProposal) => {
        // 检查当前用户是否已签
        // (先加载详情确保数据最新)
        try {
            const { data } = await proposalApi.getDetail(proposal.id)
            setSigningProposal(data)
            setSignSuccess(false)
            setSignResult(null)
            setSignModalOpen(true)
        } catch {
            toast.error('加载提案详情失败')
        }
    }


    // ─── 获取当前连接的钱包地址 ──────────────────
    const getConnectedAddress = (chain: string): string | null => {
        if (chain === 'BSC') return bscAddress || null
        return effectiveTronAddress || getTronAddress()
    }

    // ─── 检查是否已签名 ─────────────────────────
    const hasUserSigned = (proposal: MultisigProposal): boolean => {
        const addr = getConnectedAddress(proposal.chain)
        if (!addr) return false
        return proposal.signatures.some(s =>
            proposal.chain === 'BSC'
                ? s.signer_address.toLowerCase() === addr.toLowerCase()
                : s.signer_address === addr
        )
    }

    // ─── 检查是否是 owner ───────────────────────
    const isOwner = (proposal: MultisigProposal): boolean => {
        const addr = getConnectedAddress(proposal.chain)
        if (!addr || !proposal.owners) return false
        return proposal.chain === 'BSC'
            ? proposal.owners.some(o => o.toLowerCase() === addr.toLowerCase())
            : proposal.owners.includes(addr)
    }

    // ─── 执行签名 ──────────────────────────────
    const handleSign = async () => {
        if (!signingProposal) return

        const chain = signingProposal.chain
        const connectedAddr = getConnectedAddress(chain)

        if (!connectedAddr) {
            toast.error(`请先连接 ${chain} 钱包`)
            return
        }

        if (!isOwner(signingProposal)) {
            toast.error('您的钱包地址不是该多签钱包的 owner')
            return
        }

        if (hasUserSigned(signingProposal)) {
            toast.error('您已经签署过此提案')
            return
        }

        setIsSigning(true)

        try {
            let signature: string
            let signedRawDataHexRef: string | undefined
            const isHashSign = signingProposal.type === 'collection'

            if (isHashSign) {
                // ─── 归集提案：签 sha256 哈希（personal_sign） ───
                const hashToSign = signingProposal.safe_tx_hash!
                if (chain === 'BSC') {
                    // eth_sign / personal_sign
                    signature = await signMessageAsync({
                        message: { raw: hashToSign as `0x${string}` },
                    })
                } else {
                    signature = await signTronMessage(hashToSign)
                }
            } else if (chain === 'BSC') {
                // ─── Safe 转账提案：EIP-712 签名 ───
                const txData = signingProposal.tx_data as Record<string, unknown>

                const sig = await signTypedDataAsync({
                    domain: {
                        chainId: 56,
                        verifyingContract: signingProposal.wallet_address as `0x${string}`,
                    },
                    types: {
                        SafeTx: [
                            { name: 'to', type: 'address' },
                            { name: 'value', type: 'uint256' },
                            { name: 'data', type: 'bytes' },
                            { name: 'operation', type: 'uint8' },
                            { name: 'safeTxGas', type: 'uint256' },
                            { name: 'baseGas', type: 'uint256' },
                            { name: 'gasPrice', type: 'uint256' },
                            { name: 'gasToken', type: 'address' },
                            { name: 'refundReceiver', type: 'address' },
                            { name: 'nonce', type: 'uint256' },
                        ],
                    },
                    primaryType: 'SafeTx',
                    message: {
                        to: txData.to as `0x${string}`,
                        value: BigInt(txData.value as number),
                        data: txData.data as `0x${string}`,
                        operation: txData.operation as number,
                        safeTxGas: BigInt(txData.safeTxGas as number),
                        baseGas: BigInt(txData.baseGas as number),
                        gasPrice: BigInt(txData.gasPrice as number),
                        gasToken: txData.gasToken as `0x${string}`,
                        refundReceiver: txData.refundReceiver as `0x${string}`,
                        nonce: BigInt(txData.nonce as number),
                    },
                })
                signature = sig
            } else {
                const txData = signingProposal.tx_data as Record<string, unknown>
                if (txData?._no_transfer || txData?._contract_multisig) {
                    // ─── 纯审批提案 或 合约多签提案：signMessageV2(safe_tx_hash) ───
                    signature = await signTronMessage(signingProposal.safe_tx_hash!)
                } else {
                    // ─── TRON 原生多签转账提案：多签交易签名 ───
                    const transaction = JSON.parse(JSON.stringify(txData.transaction)) as Record<string, unknown>
                    {
                        const result = await signTronTransaction(transaction)
                        signature = result.signature
                        // 手机TronLink会刷新expiration字段，把实际签名的raw_data_hex传给后端
                        const signedRawDataHex = (result.signedTx as Record<string, unknown>)?.raw_data_hex as string | undefined
                        if (signedRawDataHex) {
                            signedRawDataHexRef = signedRawDataHex
                        }
                    }
                }
            }

            // 提交签名到后端
            const { data } = await proposalApi.sign(signingProposal.id, {
                signer_address: connectedAddr,
                signature,
                ...(signedRawDataHexRef ? { signed_raw_data_hex: signedRawDataHexRef } : {}),
            })

            setSignSuccess(true)
            setSignResult(data)

            if (data.auto_executed) {
                if (data.execution_tx_hash) {
                    toast.success('签名完成! 交易已执行', {
                        description: `Tx: ${data.execution_tx_hash.slice(0, 16)}...`,
                    })
                } else {
                    // 后台异步广播（归集 / BSC MultiSend / TRON 多签）
                    toast.success('签名完成，后台执行中...', {
                        description: '交易广播中，结果稍后自动更新',
                    })
                    setExecutingIds(prev => new Set(prev).add(signingProposal.id))
                }
            } else if (data.current_signatures >= data.threshold) {
                // 旧的 TRON 后台异步路径（兜底）
                toast.success('签名完成，交易执行中...', {
                    description: '后台广播 TRON 交易，结果稍后自动更新',
                })
                setExecutingIds(prev => new Set(prev).add(signingProposal.id))
            } else {
                toast.success('签名成功', {
                    description: `签名进度: ${data.current_signatures}/${data.threshold}`,
                })
            }

            setTimeout(() => {
                setSignModalOpen(false)
                setSignSuccess(false)
                setSigningProposal(null)
                fetchProposals()
            }, 2000)

        } catch (err: unknown) {
            console.error('[Sign Error]', err)
            const errMsg = (err as { response?: { data?: { detail?: string } }; message?: string; code?: number })
            if (errMsg.response?.data?.detail) {
                toast.error(errMsg.response.data.detail)
            } else if (errMsg.code === 4001 || errMsg.message?.includes('rejected') || errMsg.message?.includes('denied') || errMsg.message?.includes('cancel')) {
                toast.error('签名已取消')
            } else if (
                JSON.stringify(err).includes('__sentry_captured__') ||
                errMsg.message?.includes('无法进行此交易')
            ) {
                toast.error('当前 TRON 钱包不支持多签交易签名', {
                    description: '请使用 TronLink 钱包重试',
                    duration: 6000,
                })
            } else {
                const detail = errMsg.message || JSON.stringify(err) || '未知错误'
                toast.error(`签名失败: ${detail}`)
            }
        } finally {
            setIsSigning(false)
        }
    }

    // ─── 取消提案 ──────────────────────────────
    const handleReject = async (proposal: MultisigProposal) => {
        if (!confirm('确定要取消此提案吗?')) return
        try {
            await proposalApi.reject(proposal.id)
            toast.success('提案已取消')
            fetchProposals()
        } catch (err: unknown) {
            const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
            toast.error(detail || '取消失败')
        }
    }


    // ─── 状态标签 ──────────────────────────────
    const getStatusBadge = (status: string) => {
        switch (status) {
            case 'pending': return <Badge variant="secondary">待签名</Badge>
            case 'signing': return <Badge variant="warning">签名中</Badge>
            case 'executing': return <Badge variant="warning" className="animate-pulse">执行中...</Badge>
            case 'executed': return <Badge variant="success">已执行</Badge>
            case 'rejected': return <Badge variant="destructive">已取消</Badge>
            case 'failed': return <Badge variant="destructive">执行失败</Badge>
            case 'expired': return <Badge variant="secondary">已过期</Badge>
            default: return <Badge>{status}</Badge>
        }
    }

    const getTypeLabel = (type: string) => {
        switch (type) {
            case 'collection': return '归集转账'
            case 'transfer': return '内部转账'
            case 'payout': return '出款转账'
            default: return type
        }
    }

    const filterButtons = [
        { key: 'all', label: '全部' },
        { key: 'pending', label: '待签名' },
        { key: 'signing', label: '签名中' },
        { key: 'executed', label: '已执行' },
        { key: 'rejected', label: '已取消' },
        { key: 'expired', label: '已过期' },
    ]

    return (
        <div className="w-full flex flex-col gap-6 animate-in fade-in duration-300">
            {/* 顶部栏 */}
            <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 dark:text-white mb-1">签名中心</h1>
                    <p className="text-sm text-gray-500 dark:text-gray-400">审查提案并通过钱包授权交易</p>
                </div>
                <div className="flex items-center flex-wrap gap-2">
                    {/* BSC 钱包连接 */}
                    {bscConnected && bscAddress ? (
                        <div className="flex items-center gap-1 px-3 py-2 bg-gray-50 dark:bg-[#181a20] rounded-2xl border border-gray-200 dark:border-[#2a2d35] text-xs font-mono text-gray-600 dark:text-gray-400">
                            <span className="text-[10px] text-gray-400 mr-1">BSC</span>
                            <span>{bscAddress.slice(0, 6)}...{bscAddress.slice(-4)}</span>
                            <button
                                onClick={() => disconnectBsc()}
                                className="ml-1 text-gray-400 hover:text-red-500 transition-colors"
                                title="断开 BSC 钱包"
                            >
                                <X className="w-3 h-3" />
                            </button>
                        </div>
                    ) : (
                        <Button variant="outline" size="sm" onClick={() => openAppKit()}>
                            <Wallet className="w-3 h-3 mr-1" />
                            连接 BSC
                        </Button>
                    )}
                    {/* TRON 钱包连接（带选择器） */}
                    {effectiveTronAddress ? (
                        <div className="flex items-center gap-1 px-3 py-2 bg-gray-50 dark:bg-[#181a20] rounded-2xl border border-gray-200 dark:border-[#2a2d35] text-xs font-mono text-gray-600 dark:text-gray-400">
                            <span className="text-[10px] text-gray-400 mr-1">
                                {tronWalletId === 'okx' ? 'OKX' : tronWalletId === 'tp' ? 'TP' : 'TronLink'}
                            </span>
                            <span>{effectiveTronAddress.slice(0, 6)}...{effectiveTronAddress.slice(-4)}</span>
                            <button
                                onClick={() => { setTronAddress(null); setTronWalletPickerOpen(false) }}
                                className="ml-1 text-gray-400 hover:text-red-500 transition-colors"
                                title="断开 TRON 钱包"
                            >
                                <X className="w-3 h-3" />
                            </button>
                        </div>
                    ) : (
                        <div className="relative">
                            <Button
                                variant="outline" size="sm"
                                onClick={() => setTronWalletPickerOpen(v => !v)}
                                disabled={tronConnecting}
                            >
                                {tronConnecting ? <Loader2 className="w-3 h-3 animate-spin mr-1" /> : <Wallet className="w-3 h-3 mr-1" />}
                                连接 TRON
                            </Button>
                            {tronWalletPickerOpen && (
                                <>
                                    <div className="fixed inset-0 z-40" onClick={() => setTronWalletPickerOpen(false)} />
                                    <div className="absolute right-0 top-full mt-2 z-50 w-52 bg-white dark:bg-[#1c1f26] border border-gray-200 dark:border-[#2a2d35] rounded-xl shadow-lg overflow-hidden">
                                        <div className="px-3 py-2 text-[11px] text-gray-400 border-b border-gray-100 dark:border-[#2a2d35]">
                                            选择 TRON 钱包
                                        </div>
                                        {/* 浏览器扩展 */}
                                        {detectTronWallets().map(w => (
                                            <button
                                                key={w.id}
                                                className="w-full px-4 py-2.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors flex items-center justify-between"
                                                onClick={() => handleConnectTronWithWallet(w.id)}
                                            >
                                                <span>{w.name}</span>
                                                {w.available && !w.conflicted && (
                                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-50 dark:bg-green-500/10 text-green-600 dark:text-green-400">
                                                        已安装
                                                    </span>
                                                )}
                                                {w.conflicted && (
                                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-50 dark:bg-orange-500/10 text-orange-600 dark:text-orange-400">
                                                        ⚠ TP冲突
                                                    </span>
                                                )}
                                            </button>
                                        ))}
                                    </div>
                                </>
                            )}
                        </div>
                    )}
                    <div className="relative">
                        <Button variant="primary" size="sm" onClick={() => setCreateDropdownOpen(v => !v)}>
                            <Plus className="w-4 h-4 mr-1" /> 创建提案
                        </Button>
                        {createDropdownOpen && (
                            <>
                                <div className="fixed inset-0 z-40" onClick={() => setCreateDropdownOpen(false)} />
                                <div className="absolute right-0 top-full mt-2 z-50 w-44 bg-white dark:bg-[#1c1f26] border border-gray-200 dark:border-[#2a2d35] rounded-xl shadow-lg overflow-hidden">
                                    <button
                                        className="w-full px-4 py-2.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors"
                                        onClick={() => { setCreateDropdownOpen(false); navigate({ to: '/collections' }) }}
                                    >
                                        资金归集
                                    </button>
                                    <button
                                        className="w-full px-4 py-2.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors"
                                        onClick={() => { setCreateDropdownOpen(false); navigate({ to: '/payouts', search: { tab: 'external' } }) }}
                                    >
                                        打款汇出
                                    </button>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* 筛选栏 */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-center gap-2 overflow-x-auto hide-scrollbar">
                    {filterButtons.map((f) => (
                        <button
                            key={f.key}
                            onClick={() => handleFilterChange(f.key)}
                            className={`px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 whitespace-nowrap ${statusFilter === f.key
                                    ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900 shadow-sm'
                                    : 'bg-gray-100 dark:bg-[#1c1f26] text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-[#22252e]'
                                }`}
                        >
                            {f.label}
                        </button>
                    ))}
                </div>
                <div className="relative shrink-0">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                    <Input
                        className="pl-9 w-full sm:w-[240px]"
                        placeholder="搜索标题、地址..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                    />
                    {search && (
                        <button
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                            onClick={() => setSearch('')}
                        >
                            <X className="w-3.5 h-3.5" />
                        </button>
                    )}
                </div>
            </div>

            {/* 提案列表 */}
            {loading ? (
                <div className="flex items-center justify-center py-16">
                    <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                </div>
            ) : displayProposals.length > 0 ? (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {displayProposals.map((proposal) => (
                        <Card key={proposal.id} className="p-0 overflow-hidden flex flex-col">
                            {/* 卡片头部 */}
                            <div className="p-6 border-b border-gray-100 dark:border-[#2a2d35] flex items-start justify-between bg-gray-50/50 dark:bg-[#1c1f26]/50">
                                <div className="flex items-center gap-3">
                                    <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${proposal.type === 'payout'
                                            ? 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400'
                                            : 'bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400'
                                        }`}>
                                        {proposal.type === 'payout' ? <ShieldCheck className="w-5 h-5" /> : <ArrowRight className="w-5 h-5" />}
                                    </div>
                                    <div>
                                        <h3 className="font-bold text-zinc-900 dark:text-gray-100 text-sm">{proposal.title}</h3>
                                        <div className="text-xs text-gray-500 font-mono mt-0.5">
                                            #{proposal.id} | {proposal.chain} | {getTypeLabel(proposal.type)}
                                        </div>
                                    </div>
                                </div>
                                {executingIds.has(proposal.id)
                                    ? <Badge variant="warning" className="animate-pulse">执行中...</Badge>
                                    : getStatusBadge(proposal.status)}
                            </div>

                            {/* 卡片内容 */}
                            <div className="p-6 flex-1 flex flex-col justify-center">
                                <div className="flex justify-between items-center mb-4">
                                    <span className="text-sm text-gray-500 dark:text-gray-400">
                                        签名进度 ({proposal.threshold}/{proposal.owners?.length || '?'} 多签)
                                    </span>
                                    <span className={`text-sm font-bold ${proposal.current_signatures >= proposal.threshold
                                            ? 'text-emerald-600 dark:text-emerald-400'
                                            : 'text-zinc-900 dark:text-gray-200'
                                        }`}>
                                        {proposal.current_signatures} / {proposal.threshold}
                                    </span>
                                </div>

                                {/* 签名进度条 */}
                                <div className="w-full h-2 bg-gray-100 dark:bg-[#2a2d35] rounded-full mb-6">
                                    <div
                                        className={`h-full rounded-full transition-all duration-500 ${proposal.current_signatures >= proposal.threshold
                                                ? 'bg-emerald-500'
                                                : 'bg-blue-500'
                                            }`}
                                        style={{ width: `${Math.min(100, (proposal.current_signatures / proposal.threshold) * 100)}%` }}
                                    />
                                </div>

                                <div className="flex flex-col gap-1 mb-6">
                                    <span className="text-sm text-gray-500 dark:text-gray-400">
                                        {proposal.type === 'collection' ? '归集金额' : proposal.type === 'payout_batch' ? '打款总额' : '转账金额'}
                                    </span>
                                    <div className="text-2xl font-bold dark:text-white">
                                        {(() => {
                                            const td = proposal.tx_data as Record<string, unknown> | null
                                            if (proposal.type === 'collection' || proposal.type === 'payout_batch') {
                                                const amt = (td?._total_amount || td?.total_amount || proposal.amount) as string | undefined
                                                return amt ? Number(amt).toLocaleString() : '—'
                                            }
                                            return proposal.amount ? Number(proposal.amount).toLocaleString() : '—'
                                        })()}
                                        <span className="text-sm font-normal text-gray-500 ml-1">{proposal.token === 'native' ? (proposal.chain === 'BSC' ? 'BNB' : 'TRX') : 'USDT'}</span>
                                    </div>
                                    <div className="text-xs text-gray-400 truncate mt-1">
                                        {proposal.type === 'collection' ? (
                                            <>
                                                归集到: <span className="font-mono text-zinc-700 dark:text-gray-300 ml-1">{proposal.wallet_address || '—'}</span>
                                                {(() => {
                                                    const td = proposal.tx_data as Record<string, unknown> | null
                                                    const addrs = td?.addresses as unknown[] | undefined
                                                    return addrs ? ` (${addrs.length} 个地址)` : ''
                                                })()}
                                            </>
                                        ) : proposal.type === 'payout_batch' ? (
                                            <>
                                                {(() => {
                                                    const td = proposal.tx_data as Record<string, unknown> | null
                                                    const cnt = (td?._item_count ?? td?.item_count) as number | undefined
                                                    return `${cnt ?? '?'} 笔 → `
                                                })()}
                                                <span className="font-mono text-zinc-700 dark:text-gray-300 ml-1">
                                                    {proposal.wallet_address || '—'}
                                                </span>
                                            </>
                                        ) : (
                                            <>目标: <span className="font-mono text-zinc-700 dark:text-gray-300 ml-1">{proposal.to_address || '—'}</span></>
                                        )}
                                    </div>
                                </div>

                                {/* 操作按钮 */}
                                <div className="flex gap-2">
                                    {proposal.status === 'executed' ? (
                                        <Button variant="outline" className="flex-1 gap-2 h-11 pointer-events-none opacity-50">
                                            <CheckCircle2 className="w-4 h-4 text-emerald-500" /> 已执行
                                        </Button>
                                    ) : proposal.status === 'rejected' || proposal.status === 'expired' || proposal.status === 'failed' ? (
                                        <Button variant="outline" className="flex-1 gap-2 h-11 pointer-events-none opacity-50">
                                            <Ban className="w-4 h-4" /> {proposal.status === 'rejected' ? '已取消' : proposal.status === 'failed' ? '执行失败' : '已过期'}
                                        </Button>
                                    ) : proposal.status === 'executing' || executingIds.has(proposal.id) ? (
                                        <Button variant="outline" className="flex-1 gap-2 h-11 pointer-events-none opacity-50">
                                            <Loader2 className="w-4 h-4 animate-spin" /> 执行中...
                                        </Button>
                                    ) : (
                                        <>
                                            <Button variant="primary" className="flex-1 gap-2 h-11" onClick={() => handleOpenSign(proposal)}>
                                                <PenTool className="w-4 h-4" /> 签名
                                            </Button>
                                            <Button variant="outline" className="h-11 px-3" onClick={() => handleReject(proposal)}>
                                                <Ban className="w-4 h-4" />
                                            </Button>
                                        </>
                                    )}
                                </div>
                            </div>
                        </Card>
                    ))}
                </div>
            ) : (
                <div className="flex flex-col items-center justify-center py-16 text-gray-400 dark:text-gray-500">
                    <ShieldCheck className="w-12 h-12 mb-3 opacity-30" />
                    <p className="text-sm">暂无提案</p>
                </div>
            )}

            {/* 分页 */}
            {total > PAGE_SIZE && (
                <div className="flex items-center justify-between px-2">
                    <div className="text-sm text-gray-500">
                        共 {total} 个提案，第 {page} / {totalPages} 页
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setPage(p => Math.max(1, p - 1))}
                            disabled={page <= 1}
                            className="p-2 border border-gray-200 dark:border-[#2a2d35] rounded-lg disabled:opacity-50 hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors dark:text-gray-300"
                        >
                            <ChevronLeft className="w-4 h-4" />
                        </button>
                        <button
                            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                            disabled={page >= totalPages}
                            className="p-2 border border-gray-200 dark:border-[#2a2d35] rounded-lg disabled:opacity-50 hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors dark:text-gray-300"
                        >
                            <ChevronRight className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            )}

            {/* ─── 签名弹窗 ─────────────────────────── */}
            <Modal isOpen={signModalOpen} onClose={() => { if (!isSigning) setSignModalOpen(false) }} title="签名提案">
                {signingProposal && (
                    <div className="flex flex-col gap-6">
                        {signSuccess ? (
                            <div className="flex flex-col items-center py-6 animate-in zoom-in-75 duration-300">
                                <div className="w-16 h-16 bg-emerald-50 dark:bg-emerald-500/10 rounded-full flex items-center justify-center mb-4">
                                    <CheckCircle2 className="w-8 h-8 text-emerald-500" />
                                </div>
                                <h3 className="text-lg font-bold text-zinc-900 dark:text-white">签名成功</h3>
                                <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                                    {signResult?.auto_executed
                                        ? '交易已自动执行!'
                                        : `等待更多签名 (${signingProposal.current_signatures + 1}/${signingProposal.threshold})`
                                    }
                                </p>
                                {signResult?.execution_tx_hash && (
                                    <p className="text-xs font-mono text-gray-400 mt-2 break-all">
                                        Tx: {signResult.execution_tx_hash}
                                    </p>
                                )}
                            </div>
                        ) : (
                            <>
                                {/* 警告 */}
                                <div className="bg-red-50 dark:bg-red-900/10 p-4 rounded-xl border border-red-100 dark:border-red-900/30 text-sm text-red-800 dark:text-red-200 flex items-start gap-2.5">
                                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                                    <span><strong>警告：</strong>签名前请仔细核对目标地址和金额。签名后无法撤销。</span>
                                </div>

                                {/* 提案详情 */}
                                <div className="space-y-3">
                                    <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                        <span className="text-gray-500 text-sm">链 / 类型</span>
                                        <span className="text-sm font-medium dark:text-white">{signingProposal.chain} / {getTypeLabel(signingProposal.type)}</span>
                                    </div>

                                    {signingProposal.type === 'collection' ? (
                                        <>
                                            {/* 归集提案详情 */}
                                            <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                <span className="text-gray-500 text-sm">归集总额</span>
                                                <strong className="text-lg text-blue-600 dark:text-blue-400">
                                                    {(() => {
                                                        const td = signingProposal.tx_data as Record<string, unknown> | null
                                                        return td?.total_amount ? Number(td.total_amount).toLocaleString() : signingProposal.amount ? Number(signingProposal.amount).toLocaleString() : '—'
                                                    })()} USDT
                                                </strong>
                                            </div>
                                            <div className="flex justify-between items-center p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                <span className="text-gray-500 text-sm shrink-0 mr-4">归集目标</span>
                                                <span className="font-mono text-xs break-all font-bold text-right dark:text-white">
                                                    {(() => {
                                                        const td = signingProposal.tx_data as Record<string, unknown> | null
                                                        return (td?.target_address as string) || signingProposal.wallet_address || '—'
                                                    })()}
                                                </span>
                                            </div>
                                            {/* 归集地址列表 */}
                                            {(() => {
                                                const td = signingProposal.tx_data as Record<string, unknown> | null
                                                const addrs = td?.addresses as { address: string; amount: string }[] | undefined
                                                if (!addrs?.length) return null
                                                return (
                                                    <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                        <span className="text-gray-500 text-sm block mb-2">归集地址 ({addrs.length} 个)</span>
                                                        <div className="max-h-32 overflow-y-auto space-y-1">
                                                            {addrs.map((a, i) => (
                                                                <div key={i} className="flex justify-between text-xs py-1">
                                                                    <span className="font-mono text-gray-600 dark:text-gray-400">{a.address.slice(0, 10)}...{a.address.slice(-6)}</span>
                                                                    <span className="font-bold dark:text-white">{Number(a.amount).toLocaleString()} USDT</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    </div>
                                                )
                                            })()}
                                        </>
                                    ) : signingProposal.type === 'payout_batch' ? (
                                        <>
                                            {/* 批量打款提案详情 */}
                                            {(() => {
                                                const td = signingProposal.tx_data as Record<string, unknown> | null
                                                const totalAmt = (td?._total_amount || td?.total_amount) as string | undefined
                                                const itemCount = (td?._item_count ?? td?.item_count) as number | undefined
                                                const payoutId = (td?._payout_id || td?.payout_id) as number | undefined
                                                const walletAddr = (td?.wallet_address || td?._relay_wallet_address) as string | undefined
                                                return (
                                                    <>
                                                        <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                            <span className="text-gray-500 text-sm">打款总额</span>
                                                            <strong className="text-lg text-red-600 dark:text-red-400">
                                                                - {totalAmt ? Number(totalAmt).toLocaleString() : '—'} USDT
                                                            </strong>
                                                        </div>
                                                        <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                            <span className="text-gray-500 text-sm">笔数</span>
                                                            <span className="text-sm font-bold dark:text-white">{itemCount ?? '—'} 笔</span>
                                                        </div>
                                                        <div className="flex justify-between items-center p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                            <span className="text-gray-500 text-sm shrink-0 mr-4">打款钱包</span>
                                                            <span className="font-mono text-xs break-all font-bold text-right dark:text-white">
                                                                {(walletAddr || signingProposal.wallet_address || '—')}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between items-center p-3 bg-amber-50 dark:bg-amber-900/10 rounded-lg border border-amber-100 dark:border-amber-900/30">
                                                            <span className="text-amber-700 dark:text-amber-400 text-sm">打款详情</span>
                                                            <a
                                                                href={`/payouts/${payoutId}`}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                                                            >
                                                                查看批次 #{payoutId} →
                                                            </a>
                                                        </div>
                                                    </>
                                                )
                                            })()}
                                        </>
                                    ) : (
                                        <>
                                            {/* 转账提案详情 */}
                                            <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                <span className="text-gray-500 text-sm">金额</span>
                                                <strong className="text-lg text-red-600 dark:text-red-400">
                                                    - {signingProposal.amount ? Number(signingProposal.amount).toLocaleString() : '—'} {signingProposal.token === 'native' ? (signingProposal.chain === 'BSC' ? 'BNB' : 'TRX') : 'USDT'}
                                                </strong>
                                            </div>
                                            <div className="flex justify-between items-center p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                <span className="text-gray-500 text-sm shrink-0 mr-4">目标地址</span>
                                                <span className="font-mono text-xs break-all font-bold text-right dark:text-white">{signingProposal.to_address || '—'}</span>
                                            </div>
                                            {signingProposal.tx_data && (signingProposal.tx_data as Record<string, string>)._memo && (
                                                <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                                    <span className="text-gray-500 text-sm">备注</span>
                                                    <span className="text-sm dark:text-white">{String((signingProposal.tx_data as Record<string, string>)._memo)}</span>
                                                </div>
                                            )}
                                        </>
                                    )}

                                    <div className="flex justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                        <span className="text-gray-500 text-sm">签名进度</span>
                                        <span className="text-sm font-bold dark:text-white">{signingProposal.current_signatures} / {signingProposal.threshold}</span>
                                    </div>
                                    {/* 已签名者列表 */}
                                    {signingProposal.signatures.length > 0 && (
                                        <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg border border-gray-100 dark:border-[#2a2d35]">
                                            <span className="text-gray-500 text-sm block mb-2">已签名</span>
                                            {signingProposal.signatures.map((s) => (
                                                <div key={s.id} className="flex items-center gap-2 text-xs py-1">
                                                    <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                                                    <span className="font-mono text-gray-600 dark:text-gray-400">{s.signer_address.slice(0, 8)}...{s.signer_address.slice(-6)}</span>
                                                    {s.signer_username && <span className="text-gray-500">({s.signer_username})</span>}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {/* 钱包连接状态 */}
                                {signingProposal.chain === 'BSC' && !bscConnected && (
                                    <div className="text-center text-sm text-amber-600 dark:text-amber-400">
                                        请先通过上方按钮连接 BSC 钱包 (MetaMask)
                                    </div>
                                )}
                                {signingProposal.chain === 'TRON' && !tronAddress && (
                                    <div className="text-center text-sm text-amber-600 dark:text-amber-400">
                                        请先通过右上角「连接 TRON」按钮选择钱包并连接
                                    </div>
                                )}

                                {/* 签名按钮 */}
                                <Button
                                    variant="primary"
                                    size="lg"
                                    className="w-full h-12 gap-2"
                                    onClick={handleSign}
                                    disabled={
                                        isSigning ||
                                        (signingProposal.chain === 'BSC' && !bscConnected) ||
                                        (signingProposal.chain === 'TRON' && !effectiveTronAddress)
                                    }
                                >
                                    {isSigning ? (
                                        <><Loader2 className="w-4 h-4 animate-spin" /> 正在签名...</>
                                    ) : (
                                        <><PenTool className="w-4 h-4" /> 确认签名</>
                                    )}
                                </Button>
                            </>
                        )}
                    </div>
                )}
            </Modal>

        </div>
    )
}
