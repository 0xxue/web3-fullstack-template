import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { authApi } from '@/lib/api'

export type AdminRole = 'super_admin' | 'operator' | 'signer' | 'viewer'

interface AuthUser {
    id: number
    username: string
    role: AdminRole
    avatar: string
    totp_enabled: boolean
    google_email: string | null
    permissions: string[]
}

interface AuthState {
    isAuthenticated: boolean
    user: AuthUser | null
    accessToken: string | null
    refreshToken: string | null
    login: (username: string, password: string, totp_code?: string) => Promise<{
        success: boolean
        error?: string
        requires_2fa?: boolean
        temp_token?: string
    }>
    googleLogin: (credential: string) => Promise<{ success: boolean; error?: string }>
    logout: () => void
    changePassword: (oldPassword: string, newPassword: string) => Promise<{ success: boolean; error?: string }>
    updateUser: (updates: Partial<AuthUser>) => void
    hasPermission: (module: string) => boolean
    refreshUser: () => Promise<void>
}

export const useAuthStore = create<AuthState>()(
    persist(
        (set, get) => ({
            isAuthenticated: false,
            user: null,
            accessToken: null,
            refreshToken: null,

            login: async (username, password, totp_code?) => {
                try {
                    const { data } = await authApi.login(username, password, totp_code)

                    // 需要 2FA
                    if (data.requires_2fa && !data.access_token) {
                        return {
                            success: false,
                            requires_2fa: true,
                            temp_token: data.temp_token,
                        }
                    }

                    // 登录成功
                    set({
                        isAuthenticated: true,
                        user: data.user,
                        accessToken: data.access_token,
                        refreshToken: data.refresh_token,
                    })
                    return { success: true }
                } catch (err: unknown) {
                    const error = err as { response?: { data?: { detail?: string } } }
                    return {
                        success: false,
                        error: error.response?.data?.detail || '登录失败，请检查网络连接',
                    }
                }
            },

            googleLogin: async (credential) => {
                try {
                    const { data } = await authApi.googleLogin(credential)
                    set({
                        isAuthenticated: true,
                        user: data.user,
                        accessToken: data.access_token,
                        refreshToken: data.refresh_token,
                    })
                    return { success: true }
                } catch (err: unknown) {
                    const error = err as { response?: { data?: { detail?: string } } }
                    return {
                        success: false,
                        error: error.response?.data?.detail || 'Google 登录失败',
                    }
                }
            },

            logout: () => set({
                isAuthenticated: false,
                user: null,
                accessToken: null,
                refreshToken: null,
            }),

            updateUser: (updates) => set((state) => ({
                user: state.user ? { ...state.user, ...updates } : null,
            })),

            refreshUser: async () => {
                const { isAuthenticated } = get()
                if (!isAuthenticated) return
                try {
                    const { data } = await authApi.getMe()
                    set({ user: data })
                } catch {
                    // token 失效则登出
                    set({ isAuthenticated: false, user: null, accessToken: null, refreshToken: null })
                }
            },

            hasPermission: (module: string) => {
                const { user } = get()
                if (!user) return false
                if (user.role === 'super_admin') return true
                return (user.permissions || []).includes(module)
            },

            changePassword: async (oldPassword, newPassword) => {
                try {
                    await authApi.changePassword(oldPassword, newPassword)
                    return { success: true }
                } catch (err: unknown) {
                    const error = err as { response?: { data?: { detail?: string } } }
                    return {
                        success: false,
                        error: error.response?.data?.detail || '密码修改失败',
                    }
                }
            },
        }),
        { name: 'vault-auth-storage' }
    )
)
