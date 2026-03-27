import { Link, useRouter } from '@tanstack/react-router'
import { Vault, Bell, Sun, Moon, Menu, X, LogOut, User } from 'lucide-react'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useThemeStore } from '@/store/useThemeStore'
import { useAuthStore } from '@/store/useAuthStore'
import { toast } from 'sonner'
import { notificationApi } from '@/lib/api'
import { formatDistanceToNow } from 'date-fns'
import { zhCN } from 'date-fns/locale'

interface NotificationItem {
    id: number
    type: string
    chain: string | null
    title: string
    body: string | null
    extra_data: Record<string, string> | null
    is_read: boolean
    created_at: string
}

const TYPE_CONFIG: Record<string, { icon: string; bgClass: string }> = {
    deposit:               { icon: '💰', bgClass: 'bg-emerald-100 dark:bg-emerald-900/30' },
    large_deposit:         { icon: '⚠️', bgClass: 'bg-amber-100 dark:bg-amber-900/30' },
    proposal_created:      { icon: '📋', bgClass: 'bg-blue-100 dark:bg-blue-900/30' },
    proposal_signed:       { icon: '✍️', bgClass: 'bg-indigo-100 dark:bg-indigo-900/30' },
    proposal_executed:     { icon: '✅', bgClass: 'bg-green-100 dark:bg-green-900/30' },
    proposal_cancelled:    { icon: '❌', bgClass: 'bg-red-100 dark:bg-red-900/30' },
    collection_completed:  { icon: '📦', bgClass: 'bg-violet-100 dark:bg-violet-900/30' },
    payout_batch_created:  { icon: '💸', bgClass: 'bg-orange-100 dark:bg-orange-900/30' },
    payout_completed:      { icon: '✔️', bgClass: 'bg-teal-100 dark:bg-teal-900/30' },
    system_alert:          { icon: '🚨', bgClass: 'bg-red-100 dark:bg-red-900/30' },
}

// 兼容旧数据：title 字段存的是 type key 时，显示中文标签
const TYPE_LABELS: Record<string, string> = {
    deposit:              '新充值',
    large_deposit:        '大额充值',
    proposal_created:     '新多签提案',
    proposal_signed:      '提案签名更新',
    proposal_executed:    '提案已执行',
    proposal_cancelled:   '提案已取消',
    collection_completed: '归集完成',
    payout_batch_created: '批量打款创建',
    payout_completed:     '打款完成',
    system_alert:         '系统告警',
}

function getTypeConfig(type: string) {
    return TYPE_CONFIG[type] ?? { icon: '🔔', bgClass: 'bg-gray-100 dark:bg-gray-800' }
}

function getDisplayTitle(notif: NotificationItem): string {
    // 如果 DB 里存的 title 就是 type key（旧数据），用中文标签替换
    if (notif.title === notif.type) return TYPE_LABELS[notif.type] ?? notif.type
    return notif.title
}

function getNavigatePath(type: string): string {
    if (type.startsWith('proposal_')) return '/multisig'
    if (type.startsWith('collection_')) return '/collections'
    if (type === 'deposit' || type === 'large_deposit') return '/deposits'
    if (type.startsWith('payout_')) return '/payouts'
    return '/'
}

function formatTime(dateStr: string): string {
    try {
        return formatDistanceToNow(new Date(dateStr), { addSuffix: true, locale: zhCN })
    } catch {
        return ''
    }
}

function cleanBody(text: string | null): string {
    if (!text) return ''
    return text.replace(/[━─]{3,}/g, '').replace(/\n{3,}/g, '\n\n').trim()
}

function truncateBody(text: string | null, maxLen = 60): string {
    const cleaned = cleanBody(text)
    return cleaned.length > maxLen ? cleaned.slice(0, maxLen) + '...' : cleaned
}

export function TopNav() {
    const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false)
    const [showUserMenu, setShowUserMenu] = useState(false)
    const { isDark, toggleTheme } = useThemeStore()
    const { user, logout, hasPermission } = useAuthStore()
    const router = useRouter()
    const userMenuRef = useRef<HTMLDivElement>(null)

    // Notification state
    const [showNotifications, setShowNotifications] = useState(false)
    const [notifications, setNotifications] = useState<NotificationItem[]>([])
    const [unreadCount, setUnreadCount] = useState(0)
    const [loading, setLoading] = useState(false)
    const [filter, setFilter] = useState<'all' | 'unread'>('all')
    const [notifPage, setNotifPage] = useState(1)
    const [selectedNotif, setSelectedNotif] = useState<NotificationItem | null>(null)
    const NOTIF_PAGE_SIZE = 3
    const notifRef = useRef<HTMLDivElement>(null)

    // Apply dark class to document root
    useEffect(() => {
        if (isDark) {
            document.documentElement.classList.add('dark')
        } else {
            document.documentElement.classList.remove('dark')
        }
    }, [isDark])

    // Close user menu on outside click
    useEffect(() => {
        const handleClick = (e: MouseEvent) => {
            if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
                setShowUserMenu(false)
            }
        }
        if (showUserMenu) document.addEventListener('mousedown', handleClick)
        return () => document.removeEventListener('mousedown', handleClick)
    }, [showUserMenu])

    // Close notification panel on outside click
    useEffect(() => {
        const handleClick = (e: MouseEvent) => {
            if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
                setShowNotifications(false)
            }
        }
        if (showNotifications) document.addEventListener('mousedown', handleClick)
        return () => document.removeEventListener('mousedown', handleClick)
    }, [showNotifications])

    // 只要有任意一个 notif_* 权限，就显示通知铃
    const canViewNotifications = user?.permissions
        ? user.permissions.some((p: string) => p.startsWith('notif_'))
        : user?.role === 'super_admin'

    // Poll unread count every 30 seconds (only if user has notifications permission)
    const fetchUnreadCount = useCallback(async () => {
        if (!canViewNotifications) return
        try {
            const res = await notificationApi.unreadCount()
            setUnreadCount(res.data.count ?? 0)
        } catch {
            // ignore polling errors
        }
    }, [canViewNotifications])

    useEffect(() => {
        fetchUnreadCount()
        const interval = setInterval(fetchUnreadCount, 30000)
        return () => clearInterval(interval)
    }, [fetchUnreadCount])

    const fetchNotifications = useCallback(async (f: 'all' | 'unread') => {
        setLoading(true)
        try {
            const res = await notificationApi.list({
                page: 1,
                page_size: 50,
                unread_only: f === 'unread',
            })
            setNotifications(res.data.items ?? [])
            setUnreadCount(res.data.unread_count ?? 0)
        } catch {
            // ignore
        } finally {
            setLoading(false)
        }
    }, [])

    const handleOpenNotifications = () => {
        setNotifPage(1)
        setShowNotifications(true)
        fetchNotifications(filter)
    }

    const handleFilterChange = (f: 'all' | 'unread') => {
        setFilter(f)
        setNotifPage(1)
        fetchNotifications(f)
    }

    const handleNotifClick = async (notif: NotificationItem) => {
        if (!notif.is_read) {
            try {
                await notificationApi.markRead(notif.id)
                setNotifications(prev => prev.map(n => n.id === notif.id ? { ...n, is_read: true } : n))
                setUnreadCount(prev => Math.max(0, prev - 1))
            } catch { /* ignore */ }
        }
        setSelectedNotif({ ...notif, is_read: true })
    }

    const handleMarkAllRead = async () => {
        try {
            await notificationApi.markAllRead()
            setNotifications(prev => prev.map(n => ({ ...n, is_read: true })))
            setUnreadCount(0)
        } catch {
            toast.error('操作失败')
        }
    }

    const handleLogout = () => {
        setShowUserMenu(false)
        logout()
        toast.success('已安全退出', { description: '您已成功退出登录' })
        router.navigate({ to: '/login' })
    }

    const roleLabels: Record<string, string> = {
        super_admin: '超级管理员',
        operator: '操作员',
        signer: '签名者',
        viewer: '查看者',
    }

    const allNavLinks: { to: string; label: string; badge?: number; module?: string }[] = [
        { to: '/', label: '首页总览', module: 'dashboard' },
        { to: '/deposits', label: '充值明细', module: 'deposits' },
        { to: '/collections', label: '资金归集', module: 'collections' },
        { to: '/addresses', label: '地址库', module: 'addresses' },
        { to: '/payouts', label: '打款汇出', module: 'payouts' },
        { to: '/multisig', label: '签名中心', module: 'multisig' },
        { to: '/settings', label: '系统设置' },
    ]

    const navLinks = allNavLinks.filter(l => !l.module || hasPermission(l.module))

    const badgeLabel = unreadCount > 99 ? '99+' : String(unreadCount)
    const filteredNotifications = filter === 'unread'
        ? notifications.filter(n => !n.is_read)
        : notifications
    const notifTotalPages = Math.max(1, Math.ceil(filteredNotifications.length / NOTIF_PAGE_SIZE))
    const pagedNotifications = filteredNotifications.slice(
        (notifPage - 1) * NOTIF_PAGE_SIZE,
        notifPage * NOTIF_PAGE_SIZE,
    )

    const notif = selectedNotif

    return (
        <>
        <header className="bg-white dark:bg-[#181a20] border-b border-gray-100 dark:border-[#2a2d35] flex items-center justify-between px-4 md:px-8 h-16 sticky top-0 z-50 shrink-0 transition-colors duration-300">
            <div className="flex items-center gap-12 h-full">
                {/* Logo */}
                <div className="flex items-center gap-2">
                    <div className="w-8 h-8 bg-zinc-900 dark:bg-white rounded-lg flex items-center justify-center">
                        <Vault className="w-4 h-4 text-white dark:text-zinc-900" />
                    </div>
                    <span className="font-bold text-lg tracking-tight text-zinc-900 dark:text-white">多签管理后台</span>
                </div>

                {/* Desktop Navigation */}
                <nav className="hidden md:flex items-center gap-8 h-full text-[14px]">
                    {navLinks.map((link) => (
                        <Link
                            key={link.to}
                            to={link.to}
                            className="nav-link h-full flex items-center text-gray-500 hover:text-zinc-900 dark:text-gray-400 dark:hover:text-white [&.active]:text-zinc-900 [&.active]:font-medium dark:[&.active]:text-white"
                            activeProps={{ className: 'active' }}
                        >
                            {link.label}
                            {link.badge && (
                                <span className="ml-1.5 bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 text-[11px] font-bold px-1.5 py-0.5 rounded-full">
                                    {link.badge}
                                </span>
                            )}
                        </Link>
                    ))}
                </nav>
            </div>

            <div className="flex items-center gap-3 md:gap-5">
                {/* Theme Toggle Button */}
                <button
                    onClick={toggleTheme}
                    className="text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-[#22252e]"
                >
                    {isDark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
                </button>

                {/* Notifications — only shown when user has notifications permission */}
                {canViewNotifications && (
                <div className="relative hidden sm:block" ref={notifRef}>
                    <button
                        className="text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors relative w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-[#22252e]"
                        onClick={handleOpenNotifications}
                        aria-label="通知"
                    >
                        <Bell className="w-5 h-5" />
                        {unreadCount > 0 && (
                            <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 bg-red-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center px-0.5 border border-white dark:border-[#181a20]">
                                {badgeLabel}
                            </span>
                        )}
                    </button>

                    {/* Notification Dropdown Panel */}
                    {showNotifications && (
                        <div className="absolute right-0 top-full mt-2 w-80 bg-white dark:bg-[#181a20] rounded-xl shadow-xl border border-gray-100 dark:border-[#2a2d35] z-50 flex flex-col max-h-[480px]">
                            {/* Header */}
                            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-[#2a2d35] shrink-0">
                                <span className="text-sm font-semibold text-zinc-900 dark:text-white">通知</span>
                                <div className="flex items-center gap-2">
                                    {unreadCount > 0 && (
                                        <button
                                            onClick={handleMarkAllRead}
                                            className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                                        >
                                            全部已读
                                        </button>
                                    )}
                                    <button
                                        onClick={() => setShowNotifications(false)}
                                        className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-0.5"
                                    >
                                        <X className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>

                            {/* Filter tabs */}
                            <div className="flex items-center gap-1 px-4 py-2 border-b border-gray-100 dark:border-[#2a2d35] shrink-0">
                                <button
                                    onClick={() => handleFilterChange('all')}
                                    className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filter === 'all'
                                        ? 'bg-zinc-900 dark:bg-white text-white dark:text-zinc-900'
                                        : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-[#22252e]'
                                    }`}
                                >
                                    全部
                                </button>
                                <button
                                    onClick={() => handleFilterChange('unread')}
                                    className={`px-3 py-1 rounded-full text-xs font-medium transition-colors flex items-center gap-1 ${filter === 'unread'
                                        ? 'bg-zinc-900 dark:bg-white text-white dark:text-zinc-900'
                                        : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-[#22252e]'
                                    }`}
                                >
                                    未读
                                    {unreadCount > 0 && (
                                        <span className="bg-red-500 text-white text-[9px] font-bold rounded-full min-w-[14px] h-3.5 flex items-center justify-center px-0.5">
                                            {unreadCount > 99 ? '99+' : unreadCount}
                                        </span>
                                    )}
                                </button>
                            </div>

                            {/* Notification list */}
                            <div className="overflow-y-auto flex-1">
                                {loading ? (
                                    <div className="flex items-center justify-center py-10 text-gray-400 text-sm">
                                        加载中...
                                    </div>
                                ) : filteredNotifications.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-10 text-gray-400">
                                        <Bell className="w-8 h-8 mb-2 opacity-30" />
                                        <span className="text-sm">暂无通知</span>
                                    </div>
                                ) : (
                                    pagedNotifications.map((notif, idx) => {
                                        const cfg = getTypeConfig(notif.type)
                                        return (
                                            <div
                                                key={notif.id}
                                                onClick={() => handleNotifClick(notif)}
                                                className={`flex items-start gap-3 px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors ${idx < pagedNotifications.length - 1 ? 'border-b border-gray-50 dark:border-[#2a2d35]' : ''} ${!notif.is_read ? 'bg-blue-50/40 dark:bg-blue-900/5' : ''}`}
                                            >
                                                {/* Unread dot + icon */}
                                                <div className="relative shrink-0 mt-0.5">
                                                    {!notif.is_read && (
                                                        <span className="absolute -top-0.5 -left-0.5 w-2 h-2 bg-blue-500 rounded-full border border-white dark:border-[#181a20] z-10"></span>
                                                    )}
                                                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm ${cfg.bgClass}`}>
                                                        {cfg.icon}
                                                    </div>
                                                </div>

                                                {/* Content */}
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className={`text-xs font-medium truncate ${!notif.is_read ? 'text-zinc-900 dark:text-white' : 'text-gray-700 dark:text-gray-300'}`}>
                                                            {getDisplayTitle(notif)}
                                                        </span>
                                                        <div className="flex items-center gap-1.5 shrink-0">
                                                            {notif.chain && (
                                                                <span className="text-[10px] font-medium text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-[#2a2d35] px-1.5 py-0.5 rounded">
                                                                    {notif.chain}
                                                                </span>
                                                            )}
                                                            <span className="text-[10px] text-gray-400 dark:text-gray-500 whitespace-nowrap">
                                                                {formatTime(notif.created_at)}
                                                            </span>
                                                        </div>
                                                    </div>
                                                    {notif.body && (
                                                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-relaxed">
                                                            {truncateBody(notif.body)}
                                                        </p>
                                                    )}
                                                </div>
                                            </div>
                                        )
                                    })
                                )}
                            </div>
                            {/* Pagination — always visible */}
                            {filteredNotifications.length > 0 && (
                                <div className="flex items-center justify-between px-4 py-2.5 border-t border-gray-100 dark:border-[#2a2d35] shrink-0">
                                    <button
                                        onClick={() => setNotifPage(p => Math.max(1, p - 1))}
                                        disabled={notifPage === 1}
                                        className="text-xs text-gray-500 dark:text-gray-400 hover:text-zinc-900 dark:hover:text-white disabled:opacity-30 disabled:cursor-not-allowed px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-[#22252e] transition-colors"
                                    >
                                        ← 上一页
                                    </button>
                                    <span className="text-xs text-gray-400 dark:text-gray-500">
                                        {notifPage} / {notifTotalPages} · 共 {filteredNotifications.length} 条
                                    </span>
                                    <button
                                        onClick={() => setNotifPage(p => Math.min(notifTotalPages, p + 1))}
                                        disabled={notifPage === notifTotalPages}
                                        className="text-xs text-gray-500 dark:text-gray-400 hover:text-zinc-900 dark:hover:text-white disabled:opacity-30 disabled:cursor-not-allowed px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-[#22252e] transition-colors"
                                    >
                                        下一页 →
                                    </button>
                                </div>
                            )}
                        </div>
                    )}
                </div>
                )}

                <div className="h-5 w-px bg-gray-200 dark:bg-[#3a3e47] hidden sm:block"></div>

                {/* User Profile with Dropdown */}
                <div className="hidden sm:block relative" ref={userMenuRef}>
                    <div
                        className="flex items-center gap-2.5 cursor-pointer hover:bg-gray-50 dark:hover:bg-[#22252e] p-1.5 pr-3 rounded-full transition-colors border border-transparent hover:border-gray-100 dark:hover:border-[#3a3e47]"
                        onClick={() => setShowUserMenu(!showUserMenu)}
                    >
                        <div className="w-7 h-7 bg-blue-100 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400 rounded-full flex items-center justify-center text-xs font-bold">
                            {user?.avatar || 'U'}
                        </div>
                        <span className="text-sm font-medium text-zinc-900 dark:text-gray-200">{user?.username || '未登录'}</span>
                    </div>

                    {showUserMenu && (
                        <div className="absolute right-0 top-full mt-2 w-52 bg-white dark:bg-[#181a20] rounded-xl shadow-lg border border-gray-100 dark:border-[#2a2d35] py-1 animate-slide-down z-50">
                            <div className="px-4 py-3 border-b border-gray-100 dark:border-[#2a2d35]">
                                <div className="flex items-center gap-2.5">
                                    <div className="w-9 h-9 bg-blue-100 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400 rounded-full flex items-center justify-center text-sm font-bold">
                                        {user?.avatar || 'U'}
                                    </div>
                                    <div>
                                        <div className="text-sm font-semibold text-zinc-900 dark:text-white">{user?.username}</div>
                                        <div className="text-xs text-gray-500 dark:text-gray-400">{roleLabels[user?.role || ''] || user?.role}</div>
                                    </div>
                                </div>
                            </div>
                            <div className="py-1">
                                <button
                                    onClick={() => { setShowUserMenu(false); router.navigate({ to: '/settings' }) }}
                                    className="w-full px-4 py-2.5 text-left text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-[#22252e] transition-colors flex items-center gap-2.5"
                                >
                                    <User className="w-4 h-4" /> 账户设置
                                </button>
                                <button
                                    onClick={handleLogout}
                                    className="w-full px-4 py-2.5 text-left text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors flex items-center gap-2.5"
                                >
                                    <LogOut className="w-4 h-4" /> 退出登录
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {/* Mobile Menu Toggle */}
                <button
                    className="md:hidden text-gray-500 hover:text-zinc-900 dark:hover:text-white p-2"
                    onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                >
                    {isMobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
                </button>
            </div>

            {/* Mobile Navigation Drawer */}
            <div
                className={`fixed inset-0 z-50 flex md:hidden transition-all duration-300 ${isMobileMenuOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
                    }`}
            >
                {/* Backdrop */}
                <div
                    className="absolute inset-0 bg-black/40 backdrop-blur-sm"
                    onClick={() => setIsMobileMenuOpen(false)}
                ></div>

                {/* Drawer */}
                <div
                    className={`relative flex w-[280px] max-w-[80vw] flex-col overflow-y-auto bg-white dark:bg-[#181a20] pb-12 shadow-2xl border-r border-gray-100 dark:border-[#2a2d35] transition-transform duration-300 ease-out ${isMobileMenuOpen ? "translate-x-0" : "-translate-x-full"
                        }`}
                >
                    <div className="flex px-5 py-5 items-center justify-between border-b border-gray-100 dark:border-[#2a2d35]">
                        <div className="flex items-center gap-2">
                            <div className="w-8 h-8 bg-zinc-900 dark:bg-white rounded-lg flex items-center justify-center">
                                <Vault className="w-4 h-4 text-white dark:text-zinc-900" />
                            </div>
                            <span className="font-bold text-[17px] tracking-tight text-zinc-900 dark:text-white">多签管理后台</span>
                        </div>
                        <button
                            className="text-gray-500 hover:text-zinc-900 dark:hover:text-white p-1.5 hover:bg-gray-100 dark:hover:bg-[#22252e] rounded-full transition-colors"
                            onClick={() => setIsMobileMenuOpen(false)}
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    {/* Mobile User Info */}
                    <div className="px-5 py-4 border-b border-gray-100 dark:border-[#2a2d35]">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 bg-blue-100 dark:bg-blue-500/20 text-blue-600 dark:text-blue-400 rounded-full flex items-center justify-center text-sm font-bold">
                                {user?.avatar || 'U'}
                            </div>
                            <div>
                                <div className="text-sm font-semibold text-zinc-900 dark:text-white">{user?.username}</div>
                                <div className="text-xs text-gray-500 dark:text-gray-400">{roleLabels[user?.role || ''] || user?.role}</div>
                            </div>
                        </div>
                    </div>

                    <div className="flex-1 py-4 px-3 space-y-1">
                        {navLinks.map((link) => (
                            <Link
                                key={link.to}
                                to={link.to}
                                onClick={() => setIsMobileMenuOpen(false)}
                                className="w-full flex py-3.5 px-4 rounded-xl text-[15px] font-medium text-gray-600 dark:text-gray-300 [&.active]:text-blue-600 dark:[&.active]:text-blue-400 [&.active]:bg-blue-50 dark:[&.active]:bg-blue-500/10 justify-between items-center transition-colors"
                            >
                                <div className="flex items-center gap-3">
                                    {link.label}
                                </div>
                                {link.badge && (
                                    <span className="bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-400 text-[11px] font-bold px-2 py-0.5 rounded-full">
                                        {link.badge}
                                    </span>
                                )}
                            </Link>
                        ))}
                    </div>

                    {/* Mobile Logout */}
                    <div className="px-3 pb-4">
                        <button
                            onClick={() => { setIsMobileMenuOpen(false); handleLogout() }}
                            className="w-full flex py-3.5 px-4 rounded-xl text-[15px] font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors items-center gap-2.5"
                        >
                            <LogOut className="w-4 h-4" /> 退出登录
                        </button>
                    </div>
                </div>
            </div>
        </header>

        {/* Notification Detail Modal */}
        {notif && (
            <div
                className="fixed inset-0 z-[200] flex items-center justify-center p-4"
                onClick={() => setSelectedNotif(null)}
            >
                <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
                <div
                    className="relative bg-white dark:bg-[#181a20] rounded-2xl shadow-2xl border border-gray-100 dark:border-[#2a2d35] w-full max-w-md"
                    onClick={e => e.stopPropagation()}
                >
                    {/* Modal header */}
                    <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-[#2a2d35]">
                        <div className="flex items-center gap-3">
                            <div className={`w-9 h-9 rounded-xl flex items-center justify-center text-base ${getTypeConfig(notif.type).bgClass}`}>
                                {getTypeConfig(notif.type).icon}
                            </div>
                            <div>
                                <div className="text-sm font-semibold text-zinc-900 dark:text-white">
                                    {getDisplayTitle(notif)}
                                </div>
                                <div className="flex items-center gap-1.5 mt-0.5">
                                    {notif.chain && (
                                        <span className="text-[10px] font-medium text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-[#2a2d35] px-1.5 py-0.5 rounded">
                                            {notif.chain}
                                        </span>
                                    )}
                                    <span className="text-[10px] text-gray-400 dark:text-gray-500">
                                        {formatTime(notif.created_at)}
                                    </span>
                                </div>
                            </div>
                        </div>
                        <button
                            onClick={() => setSelectedNotif(null)}
                            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-[#22252e] transition-colors"
                        >
                            <X className="w-4 h-4" />
                        </button>
                    </div>

                    {/* Modal body */}
                    <div className="px-5 py-4">
                        {notif.body ? (
                            <pre className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-sans leading-relaxed">
                                {cleanBody(notif.body)}
                            </pre>
                        ) : (
                            <p className="text-sm text-gray-400 dark:text-gray-500">暂无详情</p>
                        )}
                    </div>

                    {/* Modal footer */}
                    <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-100 dark:border-[#2a2d35]">
                        <button
                            onClick={() => setSelectedNotif(null)}
                            className="px-4 py-2 text-sm text-gray-500 dark:text-gray-400 hover:text-zinc-900 dark:hover:text-white rounded-lg hover:bg-gray-100 dark:hover:bg-[#22252e] transition-colors"
                        >
                            关闭
                        </button>
                        <button
                            onClick={() => {
                                setSelectedNotif(null)
                                setShowNotifications(false)
                                router.navigate({ to: getNavigatePath(notif.type) })
                            }}
                            className="px-4 py-2 text-sm font-medium bg-zinc-900 dark:bg-white text-white dark:text-zinc-900 rounded-lg hover:bg-zinc-700 dark:hover:bg-gray-100 transition-colors"
                        >
                            前往相关页面 →
                        </button>
                    </div>
                </div>
            </div>
        )}
        </>
    )
}
