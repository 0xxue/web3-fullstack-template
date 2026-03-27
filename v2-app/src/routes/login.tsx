import { createFileRoute, useRouter } from '@tanstack/react-router'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Vault, Lock, Loader2, ShieldCheck } from 'lucide-react'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuthStore } from '@/store/useAuthStore'
import { settingsApi } from '@/lib/api'
import { toast } from 'sonner'

export const Route = createFileRoute('/login')({
    component: LoginComponent,
})

function LoginComponent() {
    const router = useRouter()
    const login = useAuthStore((s) => s.login)
    const googleLogin = useAuthStore((s) => s.googleLogin)
    const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
    const googleBtnRef = useRef<HTMLDivElement>(null)
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [totpCode, setTotpCode] = useState('')
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState('')
    const [shakeKey, setShakeKey] = useState(0)

    // 2FA 状态
    const [needs2FA, setNeeds2FA] = useState(false)

    // 系统设置（从后端获取）
    const [sysSettings, setSysSettings] = useState<{
        require_2fa: boolean
        enable_google_login: boolean
        google_client_id: string | null
    } | null>(null)

    useEffect(() => {
        if (isAuthenticated) {
            router.navigate({ to: '/' })
        }
    }, [isAuthenticated, router])

    // 获取公开系统设置
    useEffect(() => {
        settingsApi.getPublic()
            .then(({ data }) => setSysSettings(data))
            .catch(() => { /* 后端未启动时忽略 */ })
    }, [])

    // Google Sign-In callback
    const handleGoogleCallback = useCallback(async (response: { credential: string }) => {
        setIsLoading(true)
        setError('')
        const result = await googleLogin(response.credential)
        setIsLoading(false)
        if (result.success) {
            toast.success('Google 登录成功')
            router.navigate({ to: '/' })
        } else {
            setError(result.error || 'Google 登录失败')
            setShakeKey((k) => k + 1)
            toast.error('Google 登录失败', { description: result.error })
        }
    }, [googleLogin, router])

    // 加载 Google Identity Services SDK
    useEffect(() => {
        if (!sysSettings?.enable_google_login || !sysSettings?.google_client_id) return

        const scriptId = 'google-gsi-script'
        if (document.getElementById(scriptId)) {
            // Script already loaded, just re-initialize
            initGoogleBtn()
            return
        }

        const script = document.createElement('script')
        script.id = scriptId
        script.src = 'https://accounts.google.com/gsi/client'
        script.async = true
        script.onload = initGoogleBtn
        document.head.appendChild(script)

        function initGoogleBtn() {
            const g = (window as unknown as { google?: { accounts: { id: {
                initialize: (config: Record<string, unknown>) => void
                renderButton: (el: HTMLElement, config: Record<string, unknown>) => void
            } } } }).google
            if (!g || !googleBtnRef.current) return

            g.accounts.id.initialize({
                client_id: sysSettings!.google_client_id,
                callback: handleGoogleCallback,
            })
            googleBtnRef.current.innerHTML = ''
            g.accounts.id.renderButton(googleBtnRef.current, {
                theme: 'outline',
                size: 'large',
                width: 350,
                text: 'signin_with',
                locale: 'zh_CN',
            })
        }
    }, [sysSettings, handleGoogleCallback])

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault()
        setError('')

        if (!username.trim()) {
            setError('请输入用户名')
            setShakeKey((k) => k + 1)
            return
        }
        if (!password.trim()) {
            setError('请输入密码')
            setShakeKey((k) => k + 1)
            return
        }
        if (needs2FA && !totpCode.trim()) {
            setError('请输入两步验证码')
            setShakeKey((k) => k + 1)
            return
        }

        setIsLoading(true)
        const result = await login(username, password, needs2FA ? totpCode : undefined)
        setIsLoading(false)

        if (result.success) {
            toast.success('登录成功', { description: `欢迎回来，${username}` })
            router.navigate({ to: '/' })
        } else if (result.requires_2fa) {
            // 需要 2FA
            setNeeds2FA(true)
            toast.info('请输入两步验证码', { description: '请打开 Google Authenticator 获取验证码' })
        } else {
            setError(result.error || '登录失败')
            setShakeKey((k) => k + 1)
            toast.error('登录失败', { description: result.error })
        }
    }

    return (
        <div className="fixed inset-0 z-[100] bg-premium-login flex items-center justify-center p-4">
            <div className="w-full max-w-md animate-slide-down">
                <div className="flex flex-col items-center mb-8">
                    <div className="w-16 h-16 bg-zinc-900 dark:bg-white rounded-2xl flex items-center justify-center shadow-lg shadow-zinc-900/10 mb-6">
                        <Vault className="w-8 h-8 text-white dark:text-zinc-900" />
                    </div>
                    <h1 className="text-3xl font-bold tracking-tight text-zinc-900 dark:text-white mb-2">Vault 管理员</h1>
                    <p className="text-gray-500 dark:text-gray-400 text-center text-sm px-8">多重签名资金库管理系统</p>
                </div>

                <Card className="p-8 glass-card border border-gray-100 dark:border-[#2a2d35] bg-white/80 dark:bg-[#181a20]/80">
                    <form onSubmit={handleLogin} className="flex flex-col gap-5">
                        {!needs2FA ? (
                            <>
                                <div>
                                    <label className="text-sm font-medium text-zinc-900 dark:text-gray-200 block mb-2">管理员用户名</label>
                                    <Input
                                        placeholder="输入用户名"
                                        value={username}
                                        onChange={(e) => { setUsername(e.target.value); setError('') }}
                                        className="h-12 bg-white dark:bg-[#1c1f26]"
                                        disabled={isLoading}
                                    />
                                </div>
                                <div>
                                    <label className="text-sm font-medium text-zinc-900 dark:text-gray-200 block mb-2">密码</label>
                                    <Input
                                        type="password"
                                        placeholder="••••••••"
                                        value={password}
                                        onChange={(e) => { setPassword(e.target.value); setError('') }}
                                        className="h-12 bg-white dark:bg-[#1c1f26]"
                                        disabled={isLoading}
                                    />
                                </div>
                            </>
                        ) : (
                            <div>
                                <div className="flex items-center gap-2 mb-4 text-blue-600 dark:text-blue-400">
                                    <ShieldCheck className="w-5 h-5" />
                                    <span className="text-sm font-medium">两步验证</span>
                                </div>
                                <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                                    请打开 Google Authenticator 输入 6 位验证码
                                </p>
                                <Input
                                    placeholder="000000"
                                    value={totpCode}
                                    onChange={(e) => {
                                        const v = e.target.value.replace(/\D/g, '').slice(0, 6)
                                        setTotpCode(v)
                                        setError('')
                                    }}
                                    className="h-12 bg-white dark:bg-[#1c1f26] text-center text-2xl tracking-[0.5em] font-mono"
                                    disabled={isLoading}
                                    maxLength={6}
                                    autoFocus
                                />
                            </div>
                        )}

                        {error && (
                            <div key={shakeKey} className="animate-shake text-sm text-red-500 dark:text-red-400 bg-red-50 dark:bg-red-900/10 px-4 py-2.5 rounded-xl border border-red-100 dark:border-red-900/30">
                                {error}
                            </div>
                        )}

                        <Button variant="primary" size="lg" className="w-full h-12 mt-2 gap-2 text-[15px]" disabled={isLoading}>
                            {isLoading ? (
                                <><Loader2 className="w-4 h-4 animate-spin" /> 正在验证...</>
                            ) : needs2FA ? (
                                <><ShieldCheck className="w-4 h-4" /> 验证登录</>
                            ) : (
                                <><Lock className="w-4 h-4" /> 安全登录</>
                            )}
                        </Button>

                        {needs2FA && (
                            <button
                                type="button"
                                className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors cursor-pointer"
                                onClick={() => {
                                    setNeeds2FA(false)
                                    setTotpCode('')
                                    setError('')
                                }}
                            >
                                返回用户名密码登录
                            </button>
                        )}

                        {/* Google 登录按钮 — 仅在系统启用且配置了 Client ID 时显示 */}
                        {!needs2FA && sysSettings?.enable_google_login && sysSettings?.google_client_id && (
                            <>
                                <div className="relative my-1">
                                    <div className="absolute inset-0 flex items-center">
                                        <span className="w-full border-t border-gray-200 dark:border-gray-700" />
                                    </div>
                                    <div className="relative flex justify-center text-xs">
                                        <span className="bg-white dark:bg-[#181a20] px-3 text-gray-400">或</span>
                                    </div>
                                </div>
                                <div ref={googleBtnRef} className="w-full flex justify-center" />
                            </>
                        )}

                        <p className="text-xs text-center text-gray-400 dark:text-gray-500 mt-2">
                            仅限授权管理员登录
                        </p>
                    </form>
                </Card>
            </div>
        </div>
    )
}
