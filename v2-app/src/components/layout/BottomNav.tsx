import { Link, useLocation } from '@tanstack/react-router'
import { LayoutDashboard, ArrowDownToLine, Plus, PenTool, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/useAuthStore'

export function BottomNav() {
    const pathname = useLocation({ select: (s) => s.pathname })
    const { hasPermission } = useAuthStore()

    const allLinks = [
        { to: '/', icon: LayoutDashboard, label: '首页', module: 'dashboard' },
        { to: '/deposits', icon: ArrowDownToLine, label: '充值', module: 'deposits' },
        { to: '/payouts', icon: Plus, label: '操作', isPrimary: true, module: 'payouts' },
        { to: '/multisig', icon: PenTool, label: '签名中心', badge: true, module: 'multisig' },
        { to: '/settings', icon: Settings, label: '设置' },
    ]

    const links = allLinks.filter(l => !l.module || hasPermission(l.module))

    return (
        <div className="md:hidden fixed bottom-0 left-0 w-full bg-white dark:bg-[#181a20] border-t border-gray-100 dark:border-[#2a2d35] z-50 px-2 pb-1">
            <div className="flex items-center justify-around h-16 relative">
                {links.map((link) => {
                    const isActive = pathname === link.to
                    const Icon = link.icon

                    if (link.isPrimary) {
                        return (
                            <div key="primary-action" className="flex-1 flex justify-center h-full">
                                <Link to={link.to} className="absolute -top-6 flex flex-col items-center justify-center">
                                    <div className="w-[56px] h-[56px] bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 rounded-2xl md:rounded-[20px] shadow-lg shadow-blue-600/30 flex items-center justify-center text-white border-[6px] border-[#fafafa] dark:border-[#0f1115] transition-transform active:scale-95">
                                        <Icon className="w-6 h-6" />
                                    </div>
                                </Link>
                            </div>
                        )
                    }

                    return (
                        <Link key={link.to} to={link.to} className="flex-1 flex flex-col items-center justify-center gap-1 h-full relative group">
                            <Icon className={cn("w-[22px] h-[22px] transition-colors", isActive ? "text-zinc-900 dark:text-white" : "text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-300")} />
                            <span className={cn("text-[10px] font-semibold transition-colors", isActive ? "text-zinc-900 dark:text-white" : "text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-300")}>
                                {link.label}
                            </span>
                            {link.badge && (
                                <span className="absolute top-2.5 right-1/2 translate-x-[12px] w-2 h-2 bg-red-500 rounded-full border-2 border-white dark:border-[#181a20]"></span>
                            )}
                        </Link>
                    )
                })}
            </div>
        </div>
    )
}
