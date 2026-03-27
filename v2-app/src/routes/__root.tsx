import { createRootRoute, Outlet, useRouter, useLocation } from '@tanstack/react-router'

import { useEffect } from 'react'
import { Toaster } from 'sonner'
import { TopNav } from '@/components/layout/TopNav'
import { BottomNav } from '@/components/layout/BottomNav'
import { useAuthStore } from '@/store/useAuthStore'
import { useThemeStore } from '@/store/useThemeStore'

export const Route = createRootRoute({
    component: RootComponent,
})

function RootComponent() {
    const isDark = useThemeStore((s) => s.isDark)
    const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
    const refreshUser = useAuthStore((s) => s.refreshUser)
    const router = useRouter()
    const pathname = useLocation({ select: (s) => s.pathname })
    const isLoginPage = pathname === '/login'

    useEffect(() => {
        if (!isAuthenticated && !isLoginPage) {
            router.navigate({ to: '/login' })
        }
    }, [isAuthenticated, isLoginPage, router])

    // 页面加载时刷新用户信息（权限等）
    useEffect(() => {
        if (isAuthenticated) refreshUser()
    }, [isAuthenticated, refreshUser])

    return (
        <>
            <Toaster
                theme={isDark ? 'dark' : 'light'}
                richColors
                closeButton
                position="top-center"
                toastOptions={{
                    style: {
                        fontFamily: '"Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif',
                    },
                }}
            />

            {isLoginPage ? (
                <Outlet />
            ) : !isAuthenticated ? null : (
                <div className="min-h-screen flex flex-col bg-[#fafafa] dark:bg-[#0f1115] text-[#1a1a1a] dark:text-[#f3f4f6] font-sans">
                    <TopNav />
                    <main className="flex-1 max-w-[1400px] w-full mx-auto p-4 md:p-8 pb-24 md:pb-8 flex flex-col lg:flex-row gap-8">
                        <Outlet />
                    </main>
                    <BottomNav />
                </div>
            )}


        </>
    )
}
