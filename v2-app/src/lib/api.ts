import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const api = axios.create({
    baseURL: `${API_BASE_URL}/api`,
    timeout: 15000,
    headers: {
        'Content-Type': 'application/json',
    },
})

// 请求拦截器 — 自动带 JWT
api.interceptors.request.use((config) => {
    const storage = localStorage.getItem('vault-auth-storage')
    if (storage) {
        try {
            const parsed = JSON.parse(storage)
            const token = parsed?.state?.accessToken
            if (token) {
                config.headers.Authorization = `Bearer ${token}`
            }
        } catch {
            // ignore
        }
    }
    return config
})

// ─── Token 自动刷新拦截器 ───────────────────────────────

let isRefreshing = false
let failedQueue: { resolve: (token: string) => void; reject: (err: unknown) => void }[] = []

function processQueue(error: unknown, token: string | null) {
    failedQueue.forEach(({ resolve, reject }) => {
        if (token) resolve(token)
        else reject(error)
    })
    failedQueue = []
}

function getStoredTokens() {
    try {
        const raw = localStorage.getItem('vault-auth-storage')
        if (!raw) return null
        const parsed = JSON.parse(raw)
        return parsed?.state as { accessToken?: string; refreshToken?: string } | null
    } catch { return null }
}

function updateStoredAccessToken(newToken: string) {
    try {
        const raw = localStorage.getItem('vault-auth-storage')
        if (!raw) return
        const parsed = JSON.parse(raw)
        if (parsed?.state) {
            parsed.state.accessToken = newToken
            localStorage.setItem('vault-auth-storage', JSON.stringify(parsed))
        }
    } catch { /* ignore */ }
}

function forceLogout() {
    localStorage.removeItem('vault-auth-storage')
    if (!window.location.pathname.includes('/login')) {
        window.location.href = '/login'
    }
}

api.interceptors.response.use(
    (response) => response,
    async (error) => {
        const originalRequest = error.config

        // 非 401 或已重试过 → 直接拒绝
        if (error.response?.status !== 401 || originalRequest._retry) {
            return Promise.reject(error)
        }

        // refresh 请求本身失败 → 直接登出
        if (originalRequest.url?.includes('/auth/refresh')) {
            forceLogout()
            return Promise.reject(error)
        }

        // 正在刷新 → 排队等待
        if (isRefreshing) {
            return new Promise((resolve, reject) => {
                failedQueue.push({
                    resolve: (token: string) => {
                        originalRequest.headers.Authorization = `Bearer ${token}`
                        resolve(api(originalRequest))
                    },
                    reject,
                })
            })
        }

        originalRequest._retry = true
        isRefreshing = true

        const tokens = getStoredTokens()
        if (!tokens?.refreshToken) {
            isRefreshing = false
            forceLogout()
            return Promise.reject(error)
        }

        try {
            const { data } = await api.post('/auth/refresh', {
                refresh_token: tokens.refreshToken,
            })
            const newToken = data.access_token
            updateStoredAccessToken(newToken)
            originalRequest.headers.Authorization = `Bearer ${newToken}`
            processQueue(null, newToken)
            return api(originalRequest)
        } catch (refreshError) {
            processQueue(refreshError, null)
            forceLogout()
            return Promise.reject(refreshError)
        } finally {
            isRefreshing = false
        }
    }
)

export default api

// ─── Auth API ──────────────────────────────────────────

export const authApi = {
    login: (username: string, password: string, totp_code?: string) =>
        api.post('/auth/login', { username, password, totp_code }),

    googleLogin: (credential: string) =>
        api.post('/auth/google', { credential }),

    refresh: (refresh_token: string) =>
        api.post('/auth/refresh', { refresh_token }),

    getMe: () =>
        api.get('/auth/me'),

    changePassword: (old_password: string, new_password: string) =>
        api.post('/auth/change-password', { old_password, new_password }),

    setup2FA: () =>
        api.post('/auth/2fa/setup'),

    enable2FA: (totp_code: string) =>
        api.post('/auth/2fa/enable', { totp_code }),

    disable2FA: (totp_code: string) =>
        api.post('/auth/2fa/disable', { totp_code }),

    bindGoogleEmail: (google_email: string) =>
        api.post('/auth/google-email/bind', { google_email }),

    unbindGoogleEmail: (totp_code: string) =>
        api.post('/auth/google-email/unbind', { totp_code }),
}

// ─── Admin API ─────────────────────────────────────────

export const adminApi = {
    list: (page = 1, page_size = 20) =>
        api.get('/admins', { params: { page, page_size } }),

    create: (data: {
        username: string
        password: string
        role: string
        signer_address_bsc?: string
        signer_address_tron?: string
        tg_username?: string
        google_email?: string
    }) => api.post('/admins', data),

    update: (id: number, data: Record<string, unknown>) =>
        api.put(`/admins/${id}`, data),

    delete: (id: number) =>
        api.delete(`/admins/${id}`),

    resetPassword: (id: number, new_password: string) =>
        api.post(`/admins/${id}/reset-password`, { new_password }),

    unbindTg: (id: number) =>
        api.delete(`/admins/${id}/tg-binding`),

    kick: (id: number) =>
        api.post(`/admins/${id}/kick`),
}

// ─── System Settings API ───────────────────────────────

export const settingsApi = {
    getPublic: () =>
        api.get('/settings/public'),

    get: () =>
        api.get('/settings'),

    update: (data: Record<string, unknown>) =>
        api.put('/settings', data),

    // Telegram
    getTelegram: () =>
        api.get('/settings/telegram'),

    updateTelegram: (data: { tg_bot_token?: string; tg_admin_chat_id?: string }) =>
        api.put('/settings/telegram', data),

    testTelegram: (chat_id: string) =>
        api.post('/settings/telegram/test', { chat_id }),

    unbindTelegramGroup: () =>
        api.delete('/settings/telegram/group'),

    // 通知模板
    getNotificationTemplates: () =>
        api.get('/settings/telegram/notification-templates'),

    updateNotificationTemplates: (data: { templates: Record<string, { enabled?: boolean; template?: string; group?: boolean; dm?: boolean; threshold?: string }> }) =>
        api.put('/settings/telegram/notification-templates', data),

    resetNotificationTemplates: () =>
        api.post('/settings/telegram/notification-templates/reset'),

    // 钱包配置
    getWallets: () =>
        api.get('/settings/wallets'),

    getWalletsWithBalances: (types?: string) =>
        api.get('/settings/wallets/balances', { params: types ? { types } : undefined }),

    getFeeeBalance: () =>
        api.get('/settings/wallets/feee-balance'),

    createWallet: (data: {
        chain: string
        type: string
        address?: string
        label?: string
        derive_index?: number
    }) => api.post('/settings/wallets', data),

    updateWallet: (id: number, data: { address?: string; label?: string }) =>
        api.put(`/settings/wallets/${id}`, data),

    deleteWallet: (id: number) =>
        api.delete(`/settings/wallets/${id}`),

    exportGasKey: (id: number) =>
        api.get(`/settings/wallets/${id}/export-key`),

    // 多签钱包
    getSigners: (chain: string) =>
        api.get('/settings/multisig-wallets/signers', { params: { chain } }),

    createMultisigWallet: (data: {
        chain: string
        type: string
        label?: string
        owners: Array<{ admin_id?: number; address?: string }>
        threshold: number
        gas_wallet_id?: number
    }) => api.post('/settings/multisig-wallets/create', data),

    importMultisigWallet: (data: {
        chain: string
        type: string
        address: string
        label?: string
    }) => api.post('/settings/multisig-wallets/import', data),

    activateTronMultisig: (id: number) =>
        api.post(`/settings/multisig-wallets/${id}/activate-tron`),

    verifyMultisigWallet: (id: number) =>
        api.post(`/settings/multisig-wallets/${id}/verify`),

    // 角色权限
    getPermissions: () =>
        api.get('/settings/permissions'),

    updatePermissions: (data: { operator: string[]; signer: string[]; viewer: string[] }) =>
        api.put('/settings/permissions', data),

    // API / RPC 配置
    getApiConfig: () =>
        api.get('/settings/api-config'),

    updateApiConfig: (data: Record<string, unknown>) =>
        api.put('/settings/api-config', data),
}

// ─── Audit Log API ────────────────────────────────

export const auditApi = {
    list: (page = 1, page_size = 20, search?: string) =>
        api.get('/audit-logs', { params: { page, page_size, search } }),
}

// ─── Address API ──────────────────────────────────────

export const addressApi = {
    status: () =>
        api.get('/addresses/status'),

    list: (params: { chain?: string; page?: number; page_size?: number; search?: string }) =>
        api.get('/addresses', { params }),

    generate: (data: { chain: string; count: number; label?: string }) =>
        api.post('/addresses/generate', data),

    getDetail: (id: number) =>
        api.get(`/addresses/${id}`),

    update: (id: number, data: { label: string | null }) =>
        api.put(`/addresses/${id}`, data),

    balance: (id: number) =>
        api.get(`/addresses/${id}/balance`),
}

// ─── Deposit API ─────────────────────────────────────

export const depositApi = {
    list: (params: {
        page?: number
        page_size?: number
        chain?: string
        status?: string
        search?: string
    }) => api.get('/deposits', { params }),

    getDetail: (id: number) =>
        api.get(`/deposits/${id}`),

    stats: () =>
        api.get('/deposits/stats'),
}

// ─── Collection API ─────────────────────────────────

export const collectionApi = {
    scan: (data: { chain: string; min_amount?: number; asset_type?: string }) =>
        api.post('/collections/scan', data, { timeout: 60000 }),

    listWallets: (chain: string) =>
        api.get('/collections/wallets', { params: { chain } }),

    create: (data: { chain: string; asset_type?: string; addresses: { address: string; amount: number }[]; wallet_id?: number }) =>
        api.post('/collections', data),

    list: (params: {
        page?: number
        page_size?: number
        chain?: string
        status?: string
    }) => api.get('/collections', { params }),

    getDetail: (id: number) =>
        api.get(`/collections/${id}`),
}

// ─── Proposal API ─────────────────────────────────

export const proposalApi = {
    list: (params: {
        page?: number
        page_size?: number
        chain?: string
        status?: string
        type?: string
    }) => api.get('/proposals', { params }),

    getDetail: (id: number) =>
        api.get(`/proposals/${id}`),

    create: (data: {
        chain: string
        type: string
        wallet_id: number
        title: string
        description?: string
        to_address: string
        amount: number | string
        token?: string  // 'usdt' | 'native'
        memo?: string
    }) => api.post('/proposals', data),

    sign: (id: number, data: {
        signer_address: string
        signature: string
    }) => api.post(`/proposals/${id}/sign`, data),

    reject: (id: number) =>
        api.post(`/proposals/${id}/reject`),
}

// ─── Payout (Batch) API ───────────────────────────

export const payoutApi = {
    precheck: (data: {
        chain: string
        wallet_id: number
        items: { to_address: string; amount: string; memo?: string }[]
        asset_type?: string
    }) => api.post('/payouts/precheck', data),

    create: (data: {
        chain: string
        asset_type?: string
        wallet_id: number
        items: { to_address: string; amount: string; memo?: string }[]
        memo?: string
    }) => api.post('/payouts', data),

    list: (params: {
        page?: number
        page_size?: number
        chain?: string
        status?: string
    }) => api.get('/payouts', { params }),

    getDetail: (id: number) =>
        api.get(`/payouts/${id}`),

    getProgress: (id: number) =>
        api.get(`/payouts/${id}/progress`),

    exportCsv: (id: number, status?: string) => {
        const params = status ? `?status=${status}` : ''
        return api.get(`/payouts/${id}/export${params}`, { responseType: 'blob' })
    },
}

// ─── Notification API ─────────────────────────────

export const notificationApi = {
    list: (params?: { page?: number; page_size?: number; unread_only?: boolean }) =>
        api.get('/notifications', { params }),
    unreadCount: () =>
        api.get('/notifications/unread-count'),
    markRead: (id: number) =>
        api.post(`/notifications/${id}/read`),
    markAllRead: () =>
        api.post('/notifications/read-all'),
}

// ─── Direct Transfer (Gas Wallet) API ─────────────
export const transferApi = {
    direct: (data: {
        chain: string
        wallet_id: number
        to_address: string
        amount: string
        token: string
        memo?: string
    }) => api.post('/transfers/direct', data),

    list: (params?: { page_size?: number }) =>
        api.get('/transfers', { params }),
}
