import { createFileRoute } from '@tanstack/react-router'
import { useState, useEffect, useCallback } from 'react'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { DataTable } from '@/components/ui/data-table'
import { Modal } from '@/components/ui/modal'
import { ColumnDef } from '@tanstack/react-table'
import { Badge } from '@/components/ui/badge'
import { toast } from 'sonner'
import { Plus, Loader2, Search, X, Shield, Copy, Check, ChevronLeft, ChevronRight, Eye, EyeOff, QrCode, RefreshCw } from 'lucide-react'
import { Select } from '@/components/ui/select'
import { QRCodeSVG } from 'qrcode.react'
import { useAuthStore } from '@/store/useAuthStore'
import { adminApi, settingsApi, authApi, auditApi } from '@/lib/api'

// ─── Types ──────────────────────────────────────────

type AdminItem = {
    id: number
    username: string
    role: string
    signer_address_bsc: string | null
    signer_address_tron: string | null
    tg_username: string | null
    tg_chat_id: string | null
    google_email: string | null
    is_active: boolean
    totp_enabled: boolean
    created_at: string
    updated_at: string
}

type AuditLogItem = {
    id: number
    admin_id: number | null
    admin_username: string
    action: string
    detail: string | null
    ip_address: string | null
    created_at: string
}

// ─── Helpers ────────────────────────────────────────

const roleLabels: Record<string, string> = {
    super_admin: '超级管理员',
    operator: '操作员',
    signer: '签名者',
    viewer: '查看者',
}

const actionLabels: Record<string, string> = {
    login: '登录',
    google_login: 'Google 登录',
    change_password: '修改密码',
    enable_2fa: '启用 2FA',
    disable_2fa: '关闭 2FA',
    create_admin: '创建管理员',
    update_admin: '更新管理员',
    disable_admin: '禁用管理员',
    reset_password: '重置密码',
    update_system_settings: '更新系统设置',
    bind_google_email: '绑定 Google 邮箱',
    unbind_google_email: '解绑 Google 邮箱',
    kick_admin: '强制下线',
    update_role_permissions: '更新权限配置',
    update_telegram_config: '更新 TG 配置',
    unbind_telegram_group: '解绑 TG 群组',
    unbind_admin_tg: '解绑管理员 TG',
    update_api_config: '更新 API 配置',
    update_notification_templates: '更新通知模板',
    reset_notification_templates: '重置通知模板',
}

const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    })
}

function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: (val: boolean) => void; disabled?: boolean }) {
    return (
        <button
            onClick={() => !disabled && onChange(!checked)}
            disabled={disabled}
            className={`relative w-11 h-6 rounded-full transition-colors duration-200 ${checked ? 'bg-emerald-500' : 'bg-gray-300 dark:bg-gray-600'} ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
        >
            <div className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-transform duration-200 ${checked ? 'translate-x-5' : ''}`} />
        </button>
    )
}

// ─── Route ──────────────────────────────────────────

export const Route = createFileRoute('/settings')({
    component: SettingsComponent,
})

function SettingsComponent() {
    const { user, updateUser, hasPermission } = useAuthStore()
    const isSuperAdmin = user?.role === 'super_admin'

    const [activeTab, setActiveTab] = useState(() => {
        if (hasPermission('admin_manage')) return 'admins'
        if (hasPermission('system_params')) return 'params'
        return 'security'
    })

    // ─── Admin Management State ─────────────────────
    const [admins, setAdmins] = useState<AdminItem[]>([])
    const [loadingAdmins, setLoadingAdmins] = useState(false)
    const [addModalOpen, setAddModalOpen] = useState(false)
    const [editModalOpen, setEditModalOpen] = useState(false)
    const [editingAdmin, setEditingAdmin] = useState<AdminItem | null>(null)
    const [isAdding, setIsAdding] = useState(false)
    const [isSavingEdit, setIsSavingEdit] = useState(false)
    const [newUsername, setNewUsername] = useState('')
    const [newPassword, setNewPassword] = useState('')
    const [newRole, setNewRole] = useState('operator')
    const [newGoogleEmail, setNewGoogleEmail] = useState('')
    const [newSignerBsc, setNewSignerBsc] = useState('')
    const [newSignerTron, setNewSignerTron] = useState('')
    const [newTgUsername, setNewTgUsername] = useState('')

    // Reset password
    const [resetPwdModalOpen, setResetPwdModalOpen] = useState(false)
    const [resetPwdAdmin, setResetPwdAdmin] = useState<AdminItem | null>(null)
    const [newAdminPassword, setNewAdminPassword] = useState('')
    const [isResettingPwd, setIsResettingPwd] = useState(false)

    // ─── System Settings State ──────────────────────
    const [loadingSettings, setLoadingSettings] = useState(false)
    const [isSavingSettings, setIsSavingSettings] = useState(false)
    const [editedSettings, setEditedSettings] = useState({
        require_2fa: false,
        enable_google_login: false,
        google_client_id: '',
        session_timeout_minutes: 30,
        collection_min_bsc: '50',
        collection_min_tron: '10',
        large_deposit_threshold: '10000',
        bsc_confirmations: 15,
        tron_confirmations: 20,
        deposit_scan_interval: 30,
        native_token_monitoring: false,
    })

    // TG
    const [tgToken, setTgToken] = useState('')
    const [tgGroupId, setTgGroupId] = useState('')
    const [isSavingTg, setIsSavingTg] = useState(false)
    const [isTestingTg, setIsTestingTg] = useState(false)
    const [isSavingThreshold, setIsSavingThreshold] = useState(false)

    // TG Tab 管理
    const [tgAdmins, setTgAdmins] = useState<AdminItem[]>([])
    const [loadingTgTab, setLoadingTgTab] = useState(false)
    const [isUnbindingGroup, setIsUnbindingGroup] = useState(false)
    const [unbindingAdminId, setUnbindingAdminId] = useState<number | null>(null)

    // 通知模板
    type NotificationTypeInfo = {
        key: string; label: string; enabled: boolean; template: string
        group: boolean; dm: boolean; threshold?: string | null
        variables: { name: string; description: string }[]
    }
    const [notifTemplates, setNotifTemplates] = useState<NotificationTypeInfo[]>([])
    const [expandedNotif, setExpandedNotif] = useState<string | null>(null)
    const [isSavingNotif, setIsSavingNotif] = useState(false)
    const [isResettingNotif, setIsResettingNotif] = useState(false)

    // 权限管理
    type PermConfig = {
        all_modules: { key: string; label: string }[]
        defaults: Record<string, string[]>
        current: Record<string, string[]>
    }
    const [permConfig, setPermConfig] = useState<PermConfig | null>(null)
    const [editedPerms, setEditedPerms] = useState<Record<string, string[]>>({})
    const [loadingPerms, setLoadingPerms] = useState(false)
    const [savingPerms, setSavingPerms] = useState(false)

    // 钱包配置
    type WalletItem = {
        id: number; chain: string; type: string; address: string | null; label: string | null
        derive_index: number | null; native_balance: string | null; usdt_balance: string | null
        is_multisig: boolean; owners: string[] | null; threshold: number | null
        deployment_tx: string | null; multisig_status: string | null
        relay_wallet_id: number | null
    }
    type SignerItem = { admin_id: number; username: string; address: string | null }
    const [wallets, setWallets] = useState<WalletItem[]>([])
    const [loadingWallets, setLoadingWallets] = useState(false)
    const [loadingBalances, setLoadingBalances] = useState(false)
    const [editingWallet, setEditingWallet] = useState<WalletItem | null>(null)
    const [isSavingWallet, setIsSavingWallet] = useState(false)
    const [showCreateWallet, setShowCreateWallet] = useState(false)
    const [isCreatingWallet, setIsCreatingWallet] = useState(false)
    const [newWallet, setNewWallet] = useState({ chain: 'BSC', type: 'gas', address: '', label: '', derive_index: 0 })
    const [deletingWalletId, setDeletingWalletId] = useState<number | null>(null)
    const [qrWallet, setQrWallet] = useState<{ address: string; label: string } | null>(null)

    // 多签相关
    const [showMultisigCreate, setShowMultisigCreate] = useState(false)
    const [showMultisigImport, setShowMultisigImport] = useState(false)
    const [multisigStep, setMultisigStep] = useState(0)
    const [multisigForm, setMultisigForm] = useState({
        chain: 'BSC', type: 'collection', label: '',
        owners: [] as Array<{ admin_id?: number; address?: string; display?: string }>,
        threshold: 2, gas_wallet_id: null as number | null,
    })
    const [availableSigners, setAvailableSigners] = useState<SignerItem[]>([])
    const [manualAddress, setManualAddress] = useState('')
    const [isDeploying, setIsDeploying] = useState(false)
    const [importForm, setImportForm] = useState({ chain: 'BSC', type: 'collection', address: '', label: '' })
    const [isImporting, setIsImporting] = useState(false)
    const [activatingId, setActivatingId] = useState<number | null>(null)

    // API 配置
    const [apiConfig, setApiConfig] = useState({
        goldrush_api_keys: [''] as string[],
        bsc_rpc_urls: [''] as string[],
        tron_api_urls: [''] as string[],
        tron_api_keys: [''] as string[],
        bsc_usdt_contract: '',
        tron_usdt_contract: '',
        // TRON 能量租赁
        tron_energy_rental_enabled: false,
        tron_energy_rental_api_url: '',
        tron_energy_rental_api_key: '',
        tron_energy_rental_max_price: 420,
        tron_energy_rental_duration: 3600000,
    })
    const [loadingApiConfig, setLoadingApiConfig] = useState(false)
    const [isSavingApiConfig, setIsSavingApiConfig] = useState(false)

    // ─── Public Settings (for 2FA visibility) ────────
    const [sysRequire2FA, setSysRequire2FA] = useState(false)
    const [sysEnableGoogle, setSysEnableGoogle] = useState(false)

    // ─── Account Security State ─────────────────────
    const [oldPassword, setOldPassword] = useState('')
    const [changePwd, setChangePwd] = useState('')
    const [confirmPwd, setConfirmPwd] = useState('')
    const [isChangingPwd, setIsChangingPwd] = useState(false)

    // 2FA
    const [twoFAModalOpen, setTwoFAModalOpen] = useState(false)
    const [disableTwoFAModalOpen, setDisableTwoFAModalOpen] = useState(false)
    const [twoFASecret, setTwoFASecret] = useState('')
    const [twoFAQrUri, setTwoFAQrUri] = useState('')
    const [twoFACode, setTwoFACode] = useState('')
    const [isSettingUp2FA, setIsSettingUp2FA] = useState(false)
    const [isEnabling2FA, setIsEnabling2FA] = useState(false)
    const [isDisabling2FA, setIsDisabling2FA] = useState(false)
    const [disableCode, setDisableCode] = useState('')
    const [copied, setCopied] = useState(false)

    // Google email binding
    const [bindGoogleModalOpen, setBindGoogleModalOpen] = useState(false)
    const [unbindGoogleModalOpen, setUnbindGoogleModalOpen] = useState(false)
    const [bindGoogleEmail, setBindGoogleEmail] = useState('')
    const [isBindingGoogle, setIsBindingGoogle] = useState(false)
    const [unbindGoogleCode, setUnbindGoogleCode] = useState('')
    const [isUnbindingGoogle, setIsUnbindingGoogle] = useState(false)

    // ─── Audit Logs State ───────────────────────────
    const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([])
    const [logSearch, setLogSearch] = useState('')
    const [logPage, setLogPage] = useState(1)
    const [logTotal, setLogTotal] = useState(0)
    const [loadingLogs, setLoadingLogs] = useState(false)

    // ─── Fetch Functions ────────────────────────────

    const fetchAdmins = useCallback(async () => {
        setLoadingAdmins(true)
        try {
            const { data } = await adminApi.list(1, 100)
            setAdmins(data.items)
        } catch {
            toast.error('加载管理员列表失败')
        } finally {
            setLoadingAdmins(false)
        }
    }, [])

    const fetchSettings = useCallback(async () => {
        setLoadingSettings(true)
        try {
            const { data } = await settingsApi.get()
            setEditedSettings({
                require_2fa: data.require_2fa,
                enable_google_login: data.enable_google_login,
                google_client_id: data.google_client_id || '',
                session_timeout_minutes: data.session_timeout_minutes,
                collection_min_bsc: String(data.collection_min_bsc ?? '50'),
                collection_min_tron: String(data.collection_min_tron ?? '10'),
                large_deposit_threshold: String(data.large_deposit_threshold ?? '10000'),
                bsc_confirmations: data.bsc_confirmations ?? 15,
                tron_confirmations: data.tron_confirmations ?? 20,
                deposit_scan_interval: data.deposit_scan_interval ?? 30,
                native_token_monitoring: data.native_token_monitoring ?? false,
            })
            setTgToken(data.tg_bot_token || '')
            setTgGroupId(data.tg_admin_chat_id || '')
        } catch {
            toast.error('加载系统设置失败')
        } finally {
            setLoadingSettings(false)
        }
    }, [])

    const fetchWallets = useCallback(async () => {
        setLoadingWallets(true)
        try {
            // 先快速加载列表（无余额），再后台刷新余额
            const { data } = await settingsApi.getWallets()
            setWallets(data)
            setLoadingWallets(false)
            // 后台异步查余额
            setLoadingBalances(true)
            settingsApi.getWalletsWithBalances().then(({ data: withBal }) => {
                setWallets(withBal)
            }).catch(() => { /* 余额刷新失败不影响列表 */ }).finally(() => {
                setLoadingBalances(false)
            })
        } catch {
            toast.error('加载钱包配置失败')
            setLoadingWallets(false)
        }
    }, [])

    const fetchTgTab = useCallback(async () => {
        setLoadingTgTab(true)
        try {
            const [settingsRes, adminsRes, templatesRes] = await Promise.all([
                settingsApi.get(),
                adminApi.list(1, 100),
                settingsApi.getNotificationTemplates(),
            ])
            setTgToken(settingsRes.data.tg_bot_token || '')
            setTgGroupId(settingsRes.data.tg_admin_chat_id || '')
            setTgAdmins(adminsRes.data.items)
            setNotifTemplates(templatesRes.data.types || [])
        } catch {
            toast.error('加载 Telegram 配置失败')
        } finally {
            setLoadingTgTab(false)
        }
    }, [])

    const fetchAuditLogs = useCallback(async (page = 1, search?: string) => {
        setLoadingLogs(true)
        try {
            const { data } = await auditApi.list(page, 20, search || undefined)
            setAuditLogs(data.items)
            setLogTotal(data.total)
            setLogPage(data.page)
        } catch {
            toast.error('加载审计日志失败')
        } finally {
            setLoadingLogs(false)
        }
    }, [])

    const fetchPermissions = useCallback(async () => {
        setLoadingPerms(true)
        try {
            const { data } = await settingsApi.getPermissions()
            setPermConfig(data)
            setEditedPerms(JSON.parse(JSON.stringify(data.current)))
        } catch {
            toast.error('加载权限配置失败')
        } finally {
            setLoadingPerms(false)
        }
    }, [])

    const fetchApiConfig = useCallback(async () => {
        setLoadingApiConfig(true)
        try {
            const { data } = await settingsApi.getApiConfig()
            setApiConfig({
                goldrush_api_keys: data.goldrush_api_keys?.length ? data.goldrush_api_keys : [''],
                bsc_rpc_urls: data.bsc_rpc_urls?.length ? data.bsc_rpc_urls : [''],
                tron_api_urls: data.tron_api_urls?.length ? data.tron_api_urls : [''],
                tron_api_keys: data.tron_api_keys?.length ? data.tron_api_keys : [''],
                bsc_usdt_contract: data.bsc_usdt_contract || '',
                tron_usdt_contract: data.tron_usdt_contract || '',
                tron_energy_rental_enabled: data.tron_energy_rental_enabled ?? false,
                tron_energy_rental_api_url: data.tron_energy_rental_api_url || '',
                tron_energy_rental_api_key: data.tron_energy_rental_api_key || '',
                tron_energy_rental_max_price: data.tron_energy_rental_max_price ?? 420,
                tron_energy_rental_duration: data.tron_energy_rental_duration ?? 3600000,
            })
        } catch {
            toast.error('加载 API 配置失败')
        } finally {
            setLoadingApiConfig(false)
        }
    }, [])

    // 获取公开设置（判断 2FA 是否启用）
    useEffect(() => {
        settingsApi.getPublic().then(({ data }) => {
            setSysRequire2FA(data.require_2fa)
            setSysEnableGoogle(data.enable_google_login)
        }).catch(() => { })
    }, [])

    useEffect(() => {
        if (activeTab === 'admins' && hasPermission('admin_manage')) fetchAdmins()
        else if (activeTab === 'params' && hasPermission('system_params')) fetchSettings()
        else if (activeTab === 'wallets' && hasPermission('wallet_config')) fetchWallets()
        else if (activeTab === 'telegram' && hasPermission('telegram_config')) fetchTgTab()
        else if (activeTab === 'api-config' && hasPermission('api_config')) fetchApiConfig()
        else if (activeTab === 'logs' && hasPermission('audit_logs')) fetchAuditLogs()
        else if (activeTab === 'permissions' && isSuperAdmin) fetchPermissions()
    }, [activeTab, hasPermission, isSuperAdmin, fetchAdmins, fetchSettings, fetchWallets, fetchTgTab, fetchApiConfig, fetchAuditLogs, fetchPermissions])

    // 有 deploying 状态的钱包时，每 5 秒轮询刷新
    useEffect(() => {
        const hasDeploying = wallets.some(w => w.multisig_status === 'deploying')
        if (!hasDeploying || activeTab !== 'wallets') return
        const timer = setInterval(() => {
            settingsApi.getWallets().then(({ data }) => {
                setWallets(prev => {
                    const updated = data.map((w: any) => {
                        const old = prev.find(p => p.id === w.id)
                        return old ? { ...old, ...w } : w
                    })
                    // 如果有钱包从 deploying 变为 active，提示用户
                    for (const w of updated) {
                        const was = prev.find(p => p.id === w.id)
                        if (was?.multisig_status === 'deploying' && w.multisig_status === 'active') {
                            toast.success(`Safe 部署成功: ${w.address?.slice(0, 10)}...`)
                        } else if (was?.multisig_status === 'deploying' && w.multisig_status === 'failed') {
                            toast.error(`Safe 部署失败: ${w.label}`)
                        }
                    }
                    return updated
                })
            }).catch(() => {})
        }, 5000)
        return () => clearInterval(timer)
    }, [wallets, activeTab])

    // ─── Admin Handlers ─────────────────────────────

    const handleAddAdmin = async () => {
        if (!newUsername.trim()) { toast.error('请输入用户名'); return }
        if (!newPassword.trim() || newPassword.length < 6) { toast.error('密码至少 6 位'); return }

        setIsAdding(true)
        try {
            await adminApi.create({
                username: newUsername,
                password: newPassword,
                role: newRole,
                google_email: newGoogleEmail || undefined,
                signer_address_bsc: newSignerBsc || undefined,
                signer_address_tron: newSignerTron || undefined,
                tg_username: newTgUsername || undefined,
            })
            toast.success('管理员已添加', { description: `${newUsername} (${roleLabels[newRole]})` })
            setAddModalOpen(false)
            setNewUsername('')
            setNewPassword('')
            setNewRole('operator')
            setNewGoogleEmail('')
            setNewSignerBsc('')
            setNewSignerTron('')
            setNewTgUsername('')
            fetchAdmins()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '添加失败')
        } finally {
            setIsAdding(false)
        }
    }

    const handleOpenEdit = (admin: AdminItem) => {
        setEditingAdmin({ ...admin })
        setEditModalOpen(true)
    }

    const handleSaveEdit = async () => {
        if (!editingAdmin) return
        setIsSavingEdit(true)
        try {
            await adminApi.update(editingAdmin.id, {
                role: editingAdmin.role,
                google_email: editingAdmin.google_email,
                signer_address_bsc: editingAdmin.signer_address_bsc,
                signer_address_tron: editingAdmin.signer_address_tron,
                tg_username: editingAdmin.tg_username,
                is_active: editingAdmin.is_active,
            })
            toast.success('管理员信息已更新')
            setEditModalOpen(false)
            fetchAdmins()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '更新失败')
        } finally {
            setIsSavingEdit(false)
        }
    }

    const handleOpenResetPwd = (admin: AdminItem) => {
        setResetPwdAdmin(admin)
        setNewAdminPassword('')
        setResetPwdModalOpen(true)
    }

    const handleResetPassword = async () => {
        if (!resetPwdAdmin) return
        if (!newAdminPassword || newAdminPassword.length < 6) { toast.error('密码至少 6 位'); return }

        setIsResettingPwd(true)
        try {
            await adminApi.resetPassword(resetPwdAdmin.id, newAdminPassword)
            toast.success(`已重置 ${resetPwdAdmin.username} 的密码`)
            setResetPwdModalOpen(false)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '重置失败')
        } finally {
            setIsResettingPwd(false)
        }
    }

    // ─── System Settings Handlers ───────────────────

    const handleSaveSettings = async () => {
        setIsSavingSettings(true)
        try {
            const { data } = await settingsApi.update({
                require_2fa: editedSettings.require_2fa,
                enable_google_login: editedSettings.enable_google_login,
                google_client_id: editedSettings.google_client_id,
                session_timeout_minutes: editedSettings.session_timeout_minutes,
            })
            setEditedSettings((prev) => ({
                ...prev,
                require_2fa: data.require_2fa,
                enable_google_login: data.enable_google_login,
                google_client_id: data.google_client_id || '',
                session_timeout_minutes: data.session_timeout_minutes,
            }))
            setSysRequire2FA(data.require_2fa)
            setSysEnableGoogle(data.enable_google_login)
            toast.success('登录安全设置已更新')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '保存失败')
        } finally {
            setIsSavingSettings(false)
        }
    }

    const handleSaveTg = async () => {
        setIsSavingTg(true)
        try {
            await settingsApi.updateTelegram({
                tg_bot_token: tgToken,
                tg_admin_chat_id: tgGroupId,
            })
            toast.success('Telegram 配置已更新')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '保存失败')
        } finally {
            setIsSavingTg(false)
        }
    }

    const handleTestTg = async () => {
        if (!tgGroupId.trim()) { toast.error('请先填写通知群组 ID'); return }
        setIsTestingTg(true)
        try {
            await settingsApi.testTelegram(tgGroupId)
            toast.success('测试消息发送成功')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '发送失败')
        } finally {
            setIsTestingTg(false)
        }
    }

    const handleUnbindGroup = async () => {
        if (!confirm('确定要解除群组绑定吗？解除后群组将不再收到系统通知。')) return
        setIsUnbindingGroup(true)
        try {
            await settingsApi.unbindTelegramGroup()
            setTgGroupId('')
            toast.success('群组绑定已解除')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '解除失败')
        } finally {
            setIsUnbindingGroup(false)
        }
    }

    const handleUnbindAdminTg = async (adminId: number, username: string) => {
        if (!confirm(`确定要解除 ${username} 的 Telegram 绑定吗？`)) return
        setUnbindingAdminId(adminId)
        try {
            await adminApi.unbindTg(adminId)
            setTgAdmins(prev => prev.map(a => a.id === adminId ? { ...a, tg_chat_id: null } : a))
            toast.success(`${username} 的 Telegram 绑定已解除`)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '解除失败')
        } finally {
            setUnbindingAdminId(null)
        }
    }

    const handleSaveApiConfig = async () => {
        setIsSavingApiConfig(true)
        try {
            await settingsApi.updateApiConfig({
                ...apiConfig,
                goldrush_api_keys: apiConfig.goldrush_api_keys.filter(k => k.trim()),
                bsc_rpc_urls: apiConfig.bsc_rpc_urls.filter(u => u.trim()),
                tron_api_urls: apiConfig.tron_api_urls.filter(u => u.trim()),
                tron_api_keys: apiConfig.tron_api_keys.filter(k => k.trim()),
                tron_energy_rental_api_url: apiConfig.tron_energy_rental_api_url?.trim() || null,
                tron_energy_rental_api_key: apiConfig.tron_energy_rental_api_key?.trim() || null,
            })
            toast.success('API 配置已保存')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '保存失败')
        } finally {
            setIsSavingApiConfig(false)
        }
    }

    const handleSaveThreshold = async () => {
        setIsSavingThreshold(true)
        try {
            await settingsApi.update({
                collection_min_bsc: editedSettings.collection_min_bsc,
                collection_min_tron: editedSettings.collection_min_tron,
                large_deposit_threshold: editedSettings.large_deposit_threshold,
                bsc_confirmations: editedSettings.bsc_confirmations,
                tron_confirmations: editedSettings.tron_confirmations,
                deposit_scan_interval: editedSettings.deposit_scan_interval,
                native_token_monitoring: editedSettings.native_token_monitoring,
            })
            toast.success('运行参数已更新')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '保存失败')
        } finally {
            setIsSavingThreshold(false)
        }
    }


    const handleSaveWallet = async () => {
        if (!editingWallet) return
        setIsSavingWallet(true)
        try {
            const payload: Record<string, string | number | null | undefined> = {
                label: editingWallet.label || undefined,
            }
            if (editingWallet.type !== 'gas') {
                payload.address = editingWallet.address || undefined
            }
            if (editingWallet.is_multisig && editingWallet.chain === 'TRON') {
                payload.relay_wallet_id = editingWallet.relay_wallet_id ?? null
            }
            await settingsApi.updateWallet(editingWallet.id, payload)
            toast.success(`${editingWallet.label} 已更新`)
            setEditingWallet(null)
            fetchWallets()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '保存失败')
        } finally {
            setIsSavingWallet(false)
        }
    }

    const handleCreateWallet = async () => {
        setIsCreatingWallet(true)
        try {
            const payload: Record<string, unknown> = {
                chain: newWallet.chain,
                type: newWallet.type,
                label: newWallet.label || undefined,
            }
            if (newWallet.type !== 'gas') {
                payload.address = newWallet.address || undefined
            }
            // gas 钱包不传 derive_index，后端自动分配
            await settingsApi.createWallet(payload as Parameters<typeof settingsApi.createWallet>[0])
            toast.success('钱包创建成功')
            setShowCreateWallet(false)
            setNewWallet({ chain: 'BSC', type: 'gas', address: '', label: '', derive_index: 0 })
            fetchWallets()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '创建失败')
        } finally {
            setIsCreatingWallet(false)
        }
    }

    const [exportingKeyId, setExportingKeyId] = useState<number | null>(null)
    const [exportedKey, setExportedKey] = useState<{ address: string; private_key: string } | null>(null)
    const [showPrivateKey, setShowPrivateKey] = useState(false)

    const handleExportKey = async (walletId: number) => {
        setExportingKeyId(walletId)
        try {
            const { data } = await settingsApi.exportGasKey(walletId)
            setExportedKey(data)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '导出失败')
        } finally {
            setExportingKeyId(null)
        }
    }

    const handleDeleteWallet = async (id: number) => {
        try {
            await settingsApi.deleteWallet(id)
            toast.success('钱包已删除')
            setDeletingWalletId(null)
            fetchWallets()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '删除失败')
        }
    }

    // ─── Account Security Handlers ──────────────────

    const handleChangePassword = async () => {
        if (!oldPassword) { toast.error('请输入原密码'); return }
        if (!changePwd || changePwd.length < 6) { toast.error('新密码至少 6 位'); return }
        if (changePwd !== confirmPwd) { toast.error('两次输入的密码不一致'); return }

        setIsChangingPwd(true)
        try {
            const result = await useAuthStore.getState().changePassword(oldPassword, changePwd)
            if (result.success) {
                toast.success('密码修改成功')
                setOldPassword('')
                setChangePwd('')
                setConfirmPwd('')
            } else {
                toast.error(result.error || '密码修改失败')
            }
        } finally {
            setIsChangingPwd(false)
        }
    }

    const handleSetup2FA = async () => {
        setIsSettingUp2FA(true)
        try {
            const { data } = await authApi.setup2FA()
            setTwoFASecret(data.secret)
            setTwoFAQrUri(data.qr_uri)
            setTwoFACode('')
            setTwoFAModalOpen(true)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '获取两步验证信息失败')
        } finally {
            setIsSettingUp2FA(false)
        }
    }

    const handleEnable2FA = async () => {
        if (!twoFACode || twoFACode.length !== 6) { toast.error('请输入 6 位验证码'); return }

        setIsEnabling2FA(true)
        try {
            await authApi.enable2FA(twoFACode)
            toast.success('两步验证已启用')
            updateUser({ totp_enabled: true })
            setTwoFAModalOpen(false)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '启用失败')
        } finally {
            setIsEnabling2FA(false)
        }
    }

    const handleDisable2FA = async () => {
        if (!disableCode || disableCode.length !== 6) { toast.error('请输入 6 位验证码'); return }

        setIsDisabling2FA(true)
        try {
            await authApi.disable2FA(disableCode)
            toast.success('两步验证已关闭')
            updateUser({ totp_enabled: false })
            setDisableTwoFAModalOpen(false)
            setDisableCode('')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '关闭失败')
        } finally {
            setIsDisabling2FA(false)
        }
    }

    const handleCopySecret = () => {
        navigator.clipboard.writeText(twoFASecret)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
    }

    // ─── Google Email Handlers ────────────────────────

    const handleBindGoogleEmail = async () => {
        if (!bindGoogleEmail.trim()) { toast.error('请输入 Google 邮箱'); return }
        if (!/^[^@]+@[^@]+\.[^@]+$/.test(bindGoogleEmail)) { toast.error('邮箱格式不正确'); return }

        setIsBindingGoogle(true)
        try {
            await authApi.bindGoogleEmail(bindGoogleEmail.trim())
            toast.success('Google 邮箱绑定成功')
            updateUser({ google_email: bindGoogleEmail.trim() })
            setBindGoogleModalOpen(false)
            setBindGoogleEmail('')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '绑定失败')
        } finally {
            setIsBindingGoogle(false)
        }
    }

    const handleUnbindGoogleEmail = async () => {
        if (!unbindGoogleCode || unbindGoogleCode.length !== 6) { toast.error('请输入 6 位验证码'); return }

        setIsUnbindingGoogle(true)
        try {
            await authApi.unbindGoogleEmail(unbindGoogleCode)
            toast.success('Google 邮箱已解绑')
            updateUser({ google_email: null })
            setUnbindGoogleModalOpen(false)
            setUnbindGoogleCode('')
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } }
            toast.error(error.response?.data?.detail || '解绑失败')
        } finally {
            setIsUnbindingGoogle(false)
        }
    }

    // ─── Audit Log Handler ──────────────────────────

    const handleLogSearch = () => {
        fetchAuditLogs(1, logSearch)
    }

    // ─── Column Definitions ─────────────────────────

    const adminColumns: ColumnDef<AdminItem>[] = [
        {
            accessorKey: 'username',
            header: '用户名',
            cell: ({ row }) => <span className="font-medium text-zinc-900 dark:text-white">{row.getValue('username')}</span>,
        },
        {
            accessorKey: 'role',
            header: '角色',
            cell: ({ row }) => {
                const role = row.getValue('role') as string
                return <Badge className="capitalize">{roleLabels[role] || role}</Badge>
            },
        },
        {
            accessorKey: 'google_email',
            header: 'Google 邮箱',
            cell: ({ row }) => (
                <span className="text-sm text-gray-500">{row.original.google_email || '-'}</span>
            ),
        },
        {
            accessorKey: 'totp_enabled',
            header: '2FA',
            cell: ({ row }) => (
                <Badge variant={row.original.totp_enabled ? 'success' : 'secondary'}>
                    {row.original.totp_enabled ? '已启用' : '未启用'}
                </Badge>
            ),
        },
        {
            accessorKey: 'is_active',
            header: '状态',
            cell: ({ row }) => (
                <Badge variant={row.original.is_active ? 'success' : 'destructive'}>
                    {row.original.is_active ? '活跃' : '已禁用'}
                </Badge>
            ),
        },
        {
            accessorKey: 'created_at',
            header: '创建时间',
            cell: ({ row }) => (
                <span className="text-sm text-gray-500 whitespace-nowrap">{formatDate(row.getValue('created_at'))}</span>
            ),
        },
        {
            id: 'actions',
            cell: ({ row }) => (
                <div className="flex gap-2">
                    <Button
                        variant="ghost"
                        size="sm"
                        className="text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-500/10"
                        onClick={() => handleOpenEdit(row.original)}
                    >
                        编辑
                    </Button>
                    <Button
                        variant="ghost"
                        size="sm"
                        className="text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-500/10"
                        onClick={() => handleOpenResetPwd(row.original)}
                    >
                        重置密码
                    </Button>
                    {row.original.id !== user?.id && row.original.is_active && (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10"
                            onClick={async () => {
                                if (!confirm(`确定要强制下线 ${row.original.username} 吗？`)) return
                                try {
                                    await adminApi.kick(row.original.id)
                                    toast.success(`${row.original.username} 已被强制下线`)
                                } catch {
                                    toast.error('强制下线失败')
                                }
                            }}
                        >
                            强制下线
                        </Button>
                    )}
                </div>
            ),
        },
    ]

    const logColumns: ColumnDef<AuditLogItem>[] = [
        {
            accessorKey: 'created_at',
            header: '时间',
            cell: ({ row }) => (
                <span className="text-gray-500 whitespace-nowrap text-sm">{formatDate(row.getValue('created_at'))}</span>
            ),
        },
        {
            accessorKey: 'admin_username',
            header: '操作人',
            cell: ({ row }) => (
                <span className="font-medium text-zinc-900 dark:text-white">{row.getValue('admin_username')}</span>
            ),
        },
        {
            accessorKey: 'action',
            header: '操作',
            cell: ({ row }) => {
                const action = row.getValue('action') as string
                return <Badge variant="secondary">{actionLabels[action] || action}</Badge>
            },
        },
        {
            accessorKey: 'detail',
            header: '详细信息',
            cell: ({ row }) => (
                <span className="text-sm text-gray-600 dark:text-gray-300">{row.original.detail || '-'}</span>
            ),
        },
        {
            accessorKey: 'ip_address',
            header: 'IP 地址',
            cell: ({ row }) => (
                <span className="font-mono text-xs text-gray-500">{row.original.ip_address || '-'}</span>
            ),
        },
    ]

    const logTotalPages = Math.ceil(logTotal / 20)

    // ─── Render ─────────────────────────────────────

    return (
        <div className="w-full min-w-0 flex flex-col gap-6">
            <div>
                <h1 className="text-2xl font-bold text-zinc-900 dark:text-white mb-1">系统设置</h1>
                <p className="text-sm text-gray-500 dark:text-gray-400">管理管理员、系统配置并查看审计日志。</p>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    {hasPermission('admin_manage') && <TabsTrigger value="admins">管理员管理</TabsTrigger>}
                    {hasPermission('system_params') && <TabsTrigger value="params">系统参数</TabsTrigger>}
                    {hasPermission('wallet_config') && <TabsTrigger value="wallets">钱包配置</TabsTrigger>}
                    {hasPermission('telegram_config') && <TabsTrigger value="telegram">Telegram 机器人</TabsTrigger>}
                    {hasPermission('api_config') && <TabsTrigger value="api-config">API 配置</TabsTrigger>}
                    <TabsTrigger value="security">账户安全</TabsTrigger>
                    {hasPermission('audit_logs') && <TabsTrigger value="logs">审计日志</TabsTrigger>}
                    {isSuperAdmin && <TabsTrigger value="permissions">权限管理</TabsTrigger>}
                </TabsList>

                {/* ─── Tab 1: 管理员管理 ─── */}
                {hasPermission('admin_manage') && (
                    <TabsContent value="admins">
                        <div className="flex justify-between items-center mb-4">
                            <h3 className="font-semibold text-zinc-900 dark:text-white">管理员用户</h3>
                            <Button variant="primary" className="gap-2" onClick={() => setAddModalOpen(true)}>
                                <Plus className="w-4 h-4" /> 添加管理员
                            </Button>
                        </div>
                        {loadingAdmins ? (
                            <div className="flex justify-center py-12">
                                <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                            </div>
                        ) : (
                            <DataTable columns={adminColumns} data={admins} />
                        )}
                    </TabsContent>
                )}

                {/* ─── Tab 2: 系统参数 ─── */}
                {hasPermission('system_params') && (
                    <TabsContent value="params">
                        <div className="grid grid-cols-1 gap-6">
                            {/* 登录安全 */}
                            <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">登录安全</h3>
                                {loadingSettings ? (
                                    <div className="flex justify-center py-8">
                                        <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                                    </div>
                                ) : (
                                    <div className="space-y-5">
                                        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                            <div>
                                                <div className="text-sm font-medium text-zinc-900 dark:text-white">强制两步验证</div>
                                                <div className="text-xs text-gray-500 dark:text-gray-400">启用后所有管理员必须绑定两步验证</div>
                                            </div>
                                            <Toggle
                                                checked={editedSettings.require_2fa}
                                                onChange={(val) => setEditedSettings({ ...editedSettings, require_2fa: val })}
                                            />
                                        </div>
                                        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                            <div>
                                                <div className="text-sm font-medium text-zinc-900 dark:text-white">Google 一键登录</div>
                                                <div className="text-xs text-gray-500 dark:text-gray-400">允许绑定了 Google 邮箱的管理员通过 Google 登录</div>
                                            </div>
                                            <Toggle
                                                checked={editedSettings.enable_google_login}
                                                onChange={(val) => setEditedSettings({ ...editedSettings, enable_google_login: val })}
                                            />
                                        </div>
                                        {editedSettings.enable_google_login && (
                                            <div>
                                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">Google Client ID</label>
                                                <Input
                                                    value={editedSettings.google_client_id}
                                                    onChange={(e) => setEditedSettings({ ...editedSettings, google_client_id: e.target.value })}
                                                    placeholder="输入 Google OAuth Client ID"
                                                />
                                            </div>
                                        )}
                                        <div>
                                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">会话超时 (分钟)</label>
                                            <Input
                                                type="number"
                                                value={editedSettings.session_timeout_minutes}
                                                onChange={(e) => setEditedSettings({ ...editedSettings, session_timeout_minutes: parseInt(e.target.value) || 30 })}
                                            />
                                        </div>
                                        <Button
                                            variant="primary"
                                            className="w-full gap-2"
                                            onClick={handleSaveSettings}
                                            disabled={isSavingSettings}
                                        >
                                            {isSavingSettings ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                                            保存登录安全设置
                                        </Button>
                                    </div>
                                )}
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                {/* 归集阈值 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">归集阈值</h3>
                                    <div className="space-y-4">
                                        <div>
                                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">BSC 最低归集额 (USDT)</label>
                                            <Input type="number" value={editedSettings.collection_min_bsc} onChange={(e) => setEditedSettings({ ...editedSettings, collection_min_bsc: e.target.value })} />
                                        </div>
                                        <div>
                                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">TRON 最低归集额 (USDT)</label>
                                            <Input type="number" value={editedSettings.collection_min_tron} onChange={(e) => setEditedSettings({ ...editedSettings, collection_min_tron: e.target.value })} />
                                        </div>
                                        <div>
                                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">大额充值通知阈值 (USDT)</label>
                                            <Input type="number" value={editedSettings.large_deposit_threshold} onChange={(e) => setEditedSettings({ ...editedSettings, large_deposit_threshold: e.target.value })} />
                                        </div>
                                        <Button variant="outline" className="w-full gap-2" onClick={handleSaveThreshold} disabled={isSavingThreshold}>
                                            {isSavingThreshold ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                                            更新阈值
                                        </Button>
                                    </div>
                                </div>
                            </div>

                            {/* 区块链参数 */}
                            <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">区块链参数</h3>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                    <div>
                                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">BSC 确认区块数</label>
                                        <Input type="number" value={editedSettings.bsc_confirmations} onChange={(e) => setEditedSettings({ ...editedSettings, bsc_confirmations: parseInt(e.target.value) || 15 })} />
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">TRON 确认区块数</label>
                                        <Input type="number" value={editedSettings.tron_confirmations} onChange={(e) => setEditedSettings({ ...editedSettings, tron_confirmations: parseInt(e.target.value) || 20 })} />
                                    </div>
                                    <div>
                                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">充值扫描间隔 (秒)</label>
                                        <Input type="number" value={editedSettings.deposit_scan_interval} onChange={(e) => setEditedSettings({ ...editedSettings, deposit_scan_interval: parseInt(e.target.value) || 30 })} />
                                    </div>
                                </div>
                                <div className="flex items-center justify-between mt-4 pt-4 border-t border-gray-100 dark:border-[#2a2d35]">
                                    <div>
                                        <div className="text-sm font-medium text-gray-700 dark:text-gray-300">原生代币充值监控 (BNB/TRX)</div>
                                        <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">开启后会同时监控 BNB 和 TRX 原生转账到充值地址</div>
                                    </div>
                                    <button
                                        type="button"
                                        className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out ${editedSettings.native_token_monitoring ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'}`}
                                        onClick={async () => {
                                            const newVal = !editedSettings.native_token_monitoring
                                            setEditedSettings({ ...editedSettings, native_token_monitoring: newVal })
                                            try {
                                                await settingsApi.update({ native_token_monitoring: newVal })
                                                toast.success(newVal ? '已开启原生代币监控' : '已关闭原生代币监控')
                                            } catch {
                                                setEditedSettings(prev => ({ ...prev, native_token_monitoring: !newVal }))
                                                toast.error('保存失败')
                                            }
                                        }}
                                    >
                                        <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${editedSettings.native_token_monitoring ? 'translate-x-5' : 'translate-x-0'}`} />
                                    </button>
                                </div>
                            </div>

                        </div>
                    </TabsContent>
                )}

                {/* ─── Tab: 钱包配置 ─── */}
                {hasPermission('wallet_config') && (
                    <TabsContent value="wallets">
                        <div className="grid grid-cols-1 gap-6">
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                <p className="text-sm text-gray-500 dark:text-gray-400">配置系统钱包：Gas 钱包、归集钱包、打款钱包，支持多签。</p>
                                <div className="flex gap-2 shrink-0">
                                    <Button variant="outline" size="sm" className="gap-1" disabled={loadingBalances} onClick={() => fetchWallets()}>
                                        <RefreshCw className={`w-4 h-4 ${loadingBalances ? 'animate-spin' : ''}`} />
                                        {loadingBalances ? '刷新中...' : '刷新余额'}
                                    </Button>
                                    <Button variant="outline" size="sm" className="gap-1" onClick={() => setShowCreateWallet(true)}>
                                        <Plus className="w-4 h-4" /> Gas 钱包
                                    </Button>
                                    <Button variant="primary" size="sm" className="gap-1" onClick={() => {
                                        setMultisigStep(0)
                                        setMultisigForm({ chain: 'BSC', type: 'collection', label: '', owners: [], threshold: 2, gas_wallet_id: null })
                                        setShowMultisigCreate(true)
                                    }}>
                                        <Shield className="w-4 h-4" /> 创建多签
                                    </Button>
                                    <Button variant="outline" size="sm" className="gap-1" onClick={() => {
                                        setImportForm({ chain: 'BSC', type: 'collection', address: '', label: '' })
                                        setShowMultisigImport(true)
                                    }}>
                                        导入多签
                                    </Button>
                                </div>
                            </div>

                            {loadingWallets ? (
                                <div className="flex justify-center py-12">
                                    <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                                </div>
                            ) : (
                                <>
                                    {(['gas', 'collection', 'payout'] as const).map(walletType => {
                                        const filtered = wallets.filter(w => w.type === walletType)
                                        const typeLabel = walletType === 'gas' ? 'Gas 钱包（手续费）'
                                            : walletType === 'collection' ? '归集钱包（Safe 多签）'
                                            : '打款钱包（Safe 多签）'
                                        const nativeSymbol = (chain: string) => chain === 'BSC' ? 'BNB' : 'TRX'
                                        const fmtBal = (val: string | null) => {
                                            if (val === null || val === undefined) return '---'
                                            const n = parseFloat(val)
                                            return n < 1 ? n.toFixed(6) : n.toFixed(2)
                                        }

                                        return (
                                            <div key={walletType}>
                                                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">{typeLabel}</h3>
                                                {filtered.length === 0 ? (
                                                    <p className="text-sm text-gray-400 italic mb-4">暂未配置</p>
                                                ) : (
                                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                                                        {filtered.map(w => (
                                                            <div key={w.id} className="elegant-card p-5 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                                                <div className="flex items-center justify-between mb-3">
                                                                    <div className="flex items-center gap-2 min-w-0">
                                                                        <h4 className="font-semibold text-zinc-900 dark:text-white text-sm truncate">
                                                                            {w.label || `${w.chain} ${w.type}`}
                                                                        </h4>
                                                                        {w.is_multisig && (
                                                                            <Badge variant="default" className="shrink-0 text-[10px] px-1.5 py-0">
                                                                                {w.threshold}/{w.owners?.length} 多签
                                                                            </Badge>
                                                                        )}
                                                                    </div>
                                                                    <div className="flex items-center gap-1.5 shrink-0">
                                                                        {w.multisig_status === 'deploying' && (
                                                                            <Badge variant="default" className="text-[10px] px-1.5 py-0 animate-pulse">
                                                                                <Loader2 className="w-3 h-3 animate-spin mr-0.5 inline" />部署中
                                                                            </Badge>
                                                                        )}
                                                                        {w.multisig_status === 'failed' && (
                                                                            <Badge variant="destructive" className="text-[10px] px-1.5 py-0">部署失败</Badge>
                                                                        )}
                                                                        {w.multisig_status === 'pending_fund' && (
                                                                            <Badge variant="warning" className="text-[10px] px-1.5 py-0">待充值</Badge>
                                                                        )}
                                                                        {w.multisig_status === 'active' && w.is_multisig && (
                                                                            <Badge variant="success" className="text-[10px] px-1.5 py-0">Active</Badge>
                                                                        )}
                                                                        <Badge variant={walletType === 'gas' ? 'secondary' : walletType === 'collection' ? 'default' : 'warning'}>
                                                                            {w.chain}
                                                                        </Badge>
                                                                    </div>
                                                                </div>

                                                                {/* 地址 */}
                                                                <div className="mb-3">
                                                                    <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">
                                                                        {w.is_multisig ? '多签地址' : walletType === 'gas' ? 'HD 地址' : '钱包地址'}
                                                                        {w.derive_index !== null && w.derive_index !== undefined && ` (index: ${w.derive_index})`}
                                                                    </div>
                                                                    {w.address ? (
                                                                        <div className="flex items-start gap-1.5">
                                                                            <div className="text-xs font-mono text-zinc-700 dark:text-gray-300 break-all cursor-pointer hover:text-blue-500 dark:hover:text-blue-400 transition-colors flex-1"
                                                                                title="点击复制"
                                                                                onClick={() => { navigator.clipboard.writeText(w.address!); toast.success('地址已复制') }}>
                                                                                {w.address}
                                                                            </div>
                                                                            <button type="button" className="shrink-0 p-0.5 text-gray-400 hover:text-blue-500 transition-colors" title="显示二维码"
                                                                                onClick={() => setQrWallet({ address: w.address!, label: w.label || `${w.chain} ${w.type}` })}>
                                                                                <QrCode className="w-3.5 h-3.5" />
                                                                            </button>
                                                                        </div>
                                                                    ) : (
                                                                        <div className="text-xs font-mono text-gray-400 italic">未配置</div>
                                                                    )}
                                                                </div>

                                                                {/* 多签 owners */}
                                                                {w.is_multisig && w.owners && w.owners.length > 0 && (
                                                                    <div className="mb-3">
                                                                        <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">签名人 ({w.owners.length})</div>
                                                                        <div className="space-y-0.5">
                                                                            {w.owners.map((addr, i) => (
                                                                                <div key={i} className="text-[10px] font-mono text-gray-500 dark:text-gray-400 truncate">
                                                                                    {addr}
                                                                                </div>
                                                                            ))}
                                                                        </div>
                                                                    </div>
                                                                )}

                                                                {/* 余额 */}
                                                                {w.address && (
                                                                    <div className="flex gap-4 mb-3 text-xs">
                                                                        <div>
                                                                            <span className="text-gray-500 dark:text-gray-400">{nativeSymbol(w.chain)}: </span>
                                                                            <span className="font-medium text-zinc-900 dark:text-white">{fmtBal(w.native_balance)}</span>
                                                                        </div>
                                                                        <div>
                                                                            <span className="text-gray-500 dark:text-gray-400">USDT: </span>
                                                                            <span className="font-medium text-zinc-900 dark:text-white">{fmtBal(w.usdt_balance)}</span>
                                                                        </div>
                                                                    </div>
                                                                )}

                                                                {/* 操作 */}
                                                                <div className="flex gap-2">
                                                                    {w.multisig_status === 'deploying' ? (
                                                                        <Button variant="outline" size="sm" className="flex-1" disabled>
                                                                            <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />链上部署中...
                                                                        </Button>
                                                                    ) : w.multisig_status === 'pending_fund' ? (
                                                                        <div className="flex-1 space-y-1.5">
                                                                            <p className="text-[11px] text-yellow-600 dark:text-yellow-400">请先往上方地址转入 ≥100 TRX（链上权限修改固定手续费），然后点击激活</p>
                                                                            <Button variant="primary" size="sm" className="w-full"
                                                                                disabled={activatingId === w.id}
                                                                                onClick={async () => {
                                                                                    setActivatingId(w.id)
                                                                                    try {
                                                                                        await settingsApi.activateTronMultisig(w.id)
                                                                                        toast.success('TRON 多签激活成功')
                                                                                        fetchWallets()
                                                                                    } catch (err: any) {
                                                                                        toast.error(err?.response?.data?.detail || '激活失败')
                                                                                    } finally {
                                                                                        setActivatingId(null)
                                                                                    }
                                                                                }}>
                                                                                {activatingId === w.id ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : null}
                                                                                激活多签
                                                                            </Button>
                                                                        </div>
                                                                    ) : (
                                                                        <Button variant="outline" size="sm" className="flex-1" onClick={() => setEditingWallet({ ...w })}>
                                                                            {walletType === 'gas' ? '编辑标签' : '配置'}
                                                                        </Button>
                                                                    )}
                                                                    {walletType === 'gas' && !w.is_multisig && (
                                                                        <Button variant="outline" size="sm"
                                                                            disabled={exportingKeyId === w.id}
                                                                            onClick={() => handleExportKey(w.id)}>
                                                                            {exportingKeyId === w.id
                                                                                ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                                                                : <Shield className="w-3.5 h-3.5" />}
                                                                        </Button>
                                                                    )}
                                                                    <Button variant="outline" size="sm" className="text-red-500 hover:text-red-600 hover:border-red-300"
                                                                        onClick={() => setDeletingWalletId(w.id)}>
                                                                        <X className="w-3.5 h-3.5" />
                                                                    </Button>
                                                                </div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        )
                                    })}
                                </>
                            )}
                        </div>
                    </TabsContent>
                )}

                {/* ─── Tab: Telegram 机器人 ─── */}
                {hasPermission('telegram_config') && (
                    <TabsContent value="telegram">
                        {loadingTgTab ? (
                            <div className="flex justify-center py-12">
                                <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 gap-6">
                                {/* Bot 基础配置 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">Bot 基础配置</h3>
                                    <div className="space-y-4">
                                        <div>
                                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">机器人 Token</label>
                                            <Input type="password" value={tgToken} onChange={(e) => setTgToken(e.target.value)} placeholder="从 @BotFather 获取" />
                                            <p className="text-xs text-gray-400 mt-1">通过 Telegram @BotFather 创建机器人获取 Token</p>
                                        </div>
                                        <div className="flex gap-3">
                                            <Button variant="primary" className="flex-1 gap-2" onClick={handleSaveTg} disabled={isSavingTg}>
                                                {isSavingTg ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                                                保存 Token
                                            </Button>
                                        </div>
                                    </div>
                                </div>

                                {/* 通知群组 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <div className="flex items-center justify-between mb-4">
                                        <h3 className="font-semibold text-zinc-900 dark:text-white">通知群组</h3>
                                        {tgGroupId && (
                                            <Badge variant="success">已绑定</Badge>
                                        )}
                                    </div>
                                    {tgGroupId ? (
                                        <div className="space-y-4">
                                            <div className="p-4 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                                <div className="flex items-center justify-between">
                                                    <div>
                                                        <div className="text-sm font-medium text-zinc-900 dark:text-white">群组 Chat ID</div>
                                                        <div className="text-sm font-mono text-gray-500 dark:text-gray-400 mt-1">{tgGroupId}</div>
                                                    </div>
                                                    <div className="flex gap-2">
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={handleTestTg}
                                                            disabled={isTestingTg}
                                                        >
                                                            {isTestingTg ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : '发送测试'}
                                                        </Button>
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-500/10"
                                                            onClick={handleUnbindGroup}
                                                            disabled={isUnbindingGroup}
                                                        >
                                                            {isUnbindingGroup ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : '解除绑定'}
                                                        </Button>
                                                    </div>
                                                </div>
                                            </div>
                                            <p className="text-xs text-gray-400">群组通知包括：大额充值、新提案、签名进度、提案执行、归集完成、打款完成、系统告警</p>
                                        </div>
                                    ) : (
                                        <div className="text-center py-6">
                                            <div className="text-sm text-gray-500 dark:text-gray-400 mb-3">
                                                暂未绑定通知群组
                                            </div>
                                            <p className="text-xs text-gray-400 max-w-md mx-auto">
                                                将 Bot 拉入群组后，由超级管理员或操作员在群内发送 <code className="px-1.5 py-0.5 bg-gray-100 dark:bg-[#2a2d35] rounded text-xs">/bindgroup</code> 即可绑定
                                            </p>
                                        </div>
                                    )}
                                </div>

                                {/* 管理员私聊通知 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <div className="flex items-center justify-between mb-4">
                                        <div>
                                            <h3 className="font-semibold text-zinc-900 dark:text-white">管理员私聊通知</h3>
                                            <p className="text-xs text-gray-400 mt-1">管理员设置 TG 用户名后，给 Bot 发送 /start 即可自动绑定</p>
                                        </div>
                                        <Badge variant="secondary">{tgAdmins.filter(a => a.tg_chat_id).length}/{tgAdmins.length} 已绑定</Badge>
                                    </div>
                                    <div className="space-y-2">
                                        {tgAdmins.map(admin => (
                                            <div
                                                key={admin.id}
                                                className="flex items-center justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-xl"
                                            >
                                                <div className="flex items-center gap-3">
                                                    <div className={`w-2 h-2 rounded-full ${admin.tg_chat_id ? 'bg-emerald-500' : admin.tg_username ? 'bg-amber-500' : 'bg-gray-300 dark:bg-gray-600'}`} />
                                                    <div>
                                                        <div className="text-sm font-medium text-zinc-900 dark:text-white">
                                                            {admin.username}
                                                            <span className="text-xs text-gray-400 ml-2">{roleLabels[admin.role] || admin.role}</span>
                                                        </div>
                                                        <div className="text-xs text-gray-500 dark:text-gray-400">
                                                            {admin.tg_username ? `@${admin.tg_username}` : '未设置 TG 用户名'}
                                                            {admin.tg_chat_id && <span className="text-emerald-500 ml-2">Chat ID: {admin.tg_chat_id}</span>}
                                                            {admin.tg_username && !admin.tg_chat_id && <span className="text-amber-500 ml-2">待绑定 — 需给 Bot 发 /start</span>}
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    {admin.tg_chat_id && (
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            className="text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-500/10"
                                                            disabled={unbindingAdminId === admin.id}
                                                            onClick={() => handleUnbindAdminTg(admin.id, admin.username)}
                                                        >
                                                            {unbindingAdminId === admin.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : '解除绑定'}
                                                        </Button>
                                                    )}
                                                </div>
                                            </div>
                                        ))}
                                        {tgAdmins.length === 0 && (
                                            <div className="text-center py-6 text-sm text-gray-400">暂无管理员</div>
                                        )}
                                    </div>
                                </div>

                                {/* 通知模板配置 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <div className="flex items-center justify-between mb-4">
                                        <div>
                                            <h3 className="font-semibold text-zinc-900 dark:text-white">通知模板配置</h3>
                                            <p className="text-xs text-gray-400 mt-1">自定义每种通知的开关、渠道和消息模板</p>
                                        </div>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={isResettingNotif}
                                            onClick={async () => {
                                                if (!confirm('确定要将所有通知模板恢复为默认值吗？')) return
                                                setIsResettingNotif(true)
                                                try {
                                                    const { data } = await settingsApi.resetNotificationTemplates()
                                                    setNotifTemplates(data.types || [])
                                                    toast.success('通知模板已恢复默认')
                                                } catch { toast.error('重置失败') }
                                                finally { setIsResettingNotif(false) }
                                            }}
                                        >
                                            {isResettingNotif ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : '恢复默认'}
                                        </Button>
                                    </div>
                                    <div className="space-y-2">
                                        {notifTemplates.map((nt) => (
                                            <div key={nt.key} className="border border-gray-100 dark:border-[#2a2d35] rounded-xl overflow-hidden">
                                                {/* 行 header */}
                                                <div
                                                    className="flex items-center justify-between p-3 bg-gray-50 dark:bg-[#1c1f26] cursor-pointer"
                                                    onClick={() => setExpandedNotif(expandedNotif === nt.key ? null : nt.key)}
                                                >
                                                    <div className="flex items-center gap-3">
                                                        <Toggle
                                                            checked={nt.enabled}
                                                            onChange={(val) => {
                                                                setNotifTemplates(prev => prev.map(t => t.key === nt.key ? { ...t, enabled: val } : t))
                                                                // 自动保存开关
                                                                settingsApi.updateNotificationTemplates({
                                                                    templates: { [nt.key]: { enabled: val } }
                                                                }).catch(() => {
                                                                    setNotifTemplates(prev => prev.map(t => t.key === nt.key ? { ...t, enabled: !val } : t))
                                                                    toast.error('保存失败')
                                                                })
                                                            }}
                                                        />
                                                        <span className="text-sm font-medium text-zinc-900 dark:text-white">{nt.label}</span>
                                                        <div className="flex gap-1.5">
                                                            {nt.group && <Badge variant="secondary" className="text-[10px] px-1.5 py-0">群组</Badge>}
                                                            {nt.dm && <Badge variant="secondary" className="text-[10px] px-1.5 py-0">私聊</Badge>}
                                                        </div>
                                                    </div>
                                                    <span className="text-xs text-gray-400">{expandedNotif === nt.key ? '收起' : '编辑'}</span>
                                                </div>
                                                {/* 展开编辑区 */}
                                                {expandedNotif === nt.key && (
                                                    <div className="p-4 space-y-4 border-t border-gray-100 dark:border-[#2a2d35]">
                                                        {/* 渠道开关 */}
                                                        <div className="flex gap-6">
                                                            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                                                                <Toggle checked={nt.group} onChange={(val) => setNotifTemplates(prev => prev.map(t => t.key === nt.key ? { ...t, group: val } : t))} />
                                                                群组通知
                                                            </label>
                                                            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                                                                <Toggle checked={nt.dm} onChange={(val) => setNotifTemplates(prev => prev.map(t => t.key === nt.key ? { ...t, dm: val } : t))} />
                                                                私聊通知
                                                            </label>
                                                        </div>
                                                        {/* 大额充值阈值 */}
                                                        {nt.key === 'large_deposit' && (
                                                            <div>
                                                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">大额充值阈值 (USDT)</label>
                                                                <Input
                                                                    type="number"
                                                                    className="w-48"
                                                                    value={nt.threshold || '10000'}
                                                                    onChange={(e) => setNotifTemplates(prev => prev.map(t => t.key === 'large_deposit' ? { ...t, threshold: e.target.value } : t))}
                                                                    placeholder="10000"
                                                                />
                                                                <p className="text-xs text-gray-400 mt-1">充值金额 ≥ 此阈值时触发大额充值通知</p>
                                                            </div>
                                                        )}
                                                        {/* 模板编辑 */}
                                                        <div>
                                                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">消息模板 (支持 HTML)</label>
                                                            <textarea
                                                                className="w-full h-40 p-3 border rounded-lg bg-white dark:bg-[#1c1f26] text-sm font-mono text-zinc-900 dark:text-white border-gray-200 dark:border-[#2a2d35] resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
                                                                value={nt.template}
                                                                onChange={(e) => setNotifTemplates(prev => prev.map(t => t.key === nt.key ? { ...t, template: e.target.value } : t))}
                                                            />
                                                        </div>
                                                        {/* 可用变量 */}
                                                        <div>
                                                            <div className="text-xs text-gray-500 dark:text-gray-400 mb-2">可用变量（点击插入）：</div>
                                                            <div className="flex flex-wrap gap-1.5">
                                                                {nt.variables.map(v => (
                                                                    <button
                                                                        key={v.name}
                                                                        className="px-2 py-1 text-xs bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 rounded-md hover:bg-blue-100 dark:hover:bg-blue-500/20 transition-colors"
                                                                        title={v.description}
                                                                        onClick={() => {
                                                                            const tag = `{${v.name}}`
                                                                            setNotifTemplates(prev => prev.map(t => t.key === nt.key ? { ...t, template: t.template + tag } : t))
                                                                        }}
                                                                    >
                                                                        {`{${v.name}}`}
                                                                        <span className="text-gray-400 ml-1">{v.description}</span>
                                                                    </button>
                                                                ))}
                                                            </div>
                                                        </div>
                                                        {/* 保存按钮 */}
                                                        <div className="flex justify-end">
                                                            <Button
                                                                variant="primary"
                                                                size="sm"
                                                                disabled={isSavingNotif}
                                                                onClick={async () => {
                                                                    setIsSavingNotif(true)
                                                                    try {
                                                                        const { data } = await settingsApi.updateNotificationTemplates({
                                                                            templates: {
                                                                                [nt.key]: {
                                                                                    enabled: nt.enabled,
                                                                                    template: nt.template,
                                                                                    group: nt.group,
                                                                                    dm: nt.dm,
                                                                                    ...(nt.key === 'large_deposit' && nt.threshold ? { threshold: nt.threshold } : {}),
                                                                                }
                                                                            }
                                                                        })
                                                                        setNotifTemplates(data.types || [])
                                                                        toast.success(`${nt.label} 模板已保存`)
                                                                    } catch { toast.error('保存失败') }
                                                                    finally { setIsSavingNotif(false) }
                                                                }}
                                                            >
                                                                {isSavingNotif ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : null}
                                                                保存模板
                                                            </Button>
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                {/* 权限说明 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">权限说明</h3>
                                    <div className="space-y-3 text-sm text-gray-600 dark:text-gray-400">
                                        <div className="flex items-start gap-3 p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                            <Badge variant="default" className="mt-0.5 shrink-0">群组绑定</Badge>
                                            <span><b className="text-zinc-900 dark:text-white">超级管理员</b> 和 <b className="text-zinc-900 dark:text-white">操作员</b> 可在群内发 /bindgroup 绑定通知群组</span>
                                        </div>
                                        <div className="flex items-start gap-3 p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                            <Badge variant="default" className="mt-0.5 shrink-0">私聊绑定</Badge>
                                            <span>在「管理员管理」中为管理员填写 TG 用户名，管理员给 Bot 发送 /start 自动绑定</span>
                                        </div>
                                        <div className="flex items-start gap-3 p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                            <Badge variant="default" className="mt-0.5 shrink-0">通知范围</Badge>
                                            <span>群组通知：所有类型 | 私聊通知：大额充值、新提案、打款完成、系统告警</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}
                    </TabsContent>
                )}

                {/* ─── Tab: API 配置 ─── */}
                {hasPermission('api_config') && (
                    <TabsContent value="api-config">
                        {loadingApiConfig ? (
                            <div className="flex justify-center py-12">
                                <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 gap-6">
                                {/* Goldrush / Covalent */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white mb-1">Goldrush (Covalent) API</h3>
                                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">用于扫描 BSC 链上 USDT Transfer 事件（多 Key 轮换，额度用完自动切下一个）</p>
                                    <div className="space-y-4">
                                        <div>
                                            <div className="flex items-center justify-between mb-2">
                                                <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">API Key 列表</label>
                                                <Button
                                                    variant="ghost"
                                                    className="h-7 px-2 text-xs gap-1"
                                                    onClick={() => setApiConfig(prev => ({ ...prev, goldrush_api_keys: [...prev.goldrush_api_keys, ''] }))}
                                                >
                                                    <Plus className="w-3 h-3" /> 添加 Key
                                                </Button>
                                            </div>
                                            <div className="space-y-2">
                                                {apiConfig.goldrush_api_keys.map((key, i) => (
                                                    <div key={i} className="flex gap-2 items-center">
                                                        <span className="text-xs text-gray-400 w-5 shrink-0">#{i + 1}</span>
                                                        <Input
                                                            type="password"
                                                            placeholder="cqt_xxxxxxxxxxxx"
                                                            value={key}
                                                            onChange={(e) => {
                                                                const keys = [...apiConfig.goldrush_api_keys]
                                                                keys[i] = e.target.value
                                                                setApiConfig(prev => ({ ...prev, goldrush_api_keys: keys }))
                                                            }}
                                                            className="font-mono text-sm"
                                                        />
                                                        {apiConfig.goldrush_api_keys.length > 1 && (
                                                            <Button
                                                                variant="ghost"
                                                                className="h-8 w-8 p-0 shrink-0 text-gray-400 hover:text-red-500"
                                                                onClick={() => {
                                                                    const keys = apiConfig.goldrush_api_keys.filter((_, idx) => idx !== i)
                                                                    setApiConfig(prev => ({ ...prev, goldrush_api_keys: keys }))
                                                                }}
                                                            >
                                                                <X className="w-3.5 h-3.5" />
                                                            </Button>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* BSC 配置 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white mb-1">BSC (BNB Smart Chain)</h3>
                                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">BSC 链的 RPC 节点（按优先级排列，失败自动降级到下一个）</p>
                                    <div className="space-y-4">
                                        <div>
                                            <div className="flex items-center justify-between mb-2">
                                                <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">RPC 节点列表</label>
                                                <Button
                                                    variant="ghost"
                                                    className="h-7 px-2 text-xs gap-1"
                                                    onClick={() => setApiConfig(prev => ({ ...prev, bsc_rpc_urls: [...prev.bsc_rpc_urls, ''] }))}
                                                >
                                                    <Plus className="w-3 h-3" /> 添加节点
                                                </Button>
                                            </div>
                                            <div className="space-y-2">
                                                {apiConfig.bsc_rpc_urls.map((url, i) => (
                                                    <div key={i} className="flex gap-2 items-center">
                                                        <span className="text-xs text-gray-400 w-5 shrink-0">#{i + 1}</span>
                                                        <Input
                                                            placeholder="https://bsc-dataseed1.binance.org"
                                                            value={url}
                                                            onChange={(e) => {
                                                                const urls = [...apiConfig.bsc_rpc_urls]
                                                                urls[i] = e.target.value
                                                                setApiConfig(prev => ({ ...prev, bsc_rpc_urls: urls }))
                                                            }}
                                                            className="font-mono text-sm"
                                                        />
                                                        {apiConfig.bsc_rpc_urls.length > 1 && (
                                                            <button
                                                                onClick={() => setApiConfig(prev => ({ ...prev, bsc_rpc_urls: prev.bsc_rpc_urls.filter((_, j) => j !== i) }))}
                                                                className="text-gray-400 hover:text-red-500 shrink-0"
                                                            >
                                                                <X className="w-4 h-4" />
                                                            </button>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                        <div>
                                            <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">USDT 合约地址</label>
                                            <Input
                                                placeholder="0x55d398326f99059fF775485246999027B3197955"
                                                value={apiConfig.bsc_usdt_contract}
                                                onChange={(e) => setApiConfig(prev => ({ ...prev, bsc_usdt_contract: e.target.value }))}
                                                className="font-mono text-sm"
                                            />
                                        </div>
                                    </div>
                                </div>

                                {/* TRON 配置 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white mb-1">TRON</h3>
                                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">TRON 链的 API 节点（按优先级排列，失败自动降级到下一个）</p>
                                    <div className="space-y-4">
                                        <div>
                                            <div className="flex items-center justify-between mb-2">
                                                <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">API 节点列表</label>
                                                <Button
                                                    variant="ghost"
                                                    className="h-7 px-2 text-xs gap-1"
                                                    onClick={() => setApiConfig(prev => ({ ...prev, tron_api_urls: [...prev.tron_api_urls, ''] }))}
                                                >
                                                    <Plus className="w-3 h-3" /> 添加节点
                                                </Button>
                                            </div>
                                            <div className="space-y-2">
                                                {apiConfig.tron_api_urls.map((url, i) => (
                                                    <div key={i} className="flex gap-2 items-center">
                                                        <span className="text-xs text-gray-400 w-5 shrink-0">#{i + 1}</span>
                                                        <Input
                                                            placeholder="https://api.trongrid.io"
                                                            value={url}
                                                            onChange={(e) => {
                                                                const urls = [...apiConfig.tron_api_urls]
                                                                urls[i] = e.target.value
                                                                setApiConfig(prev => ({ ...prev, tron_api_urls: urls }))
                                                            }}
                                                            className="font-mono text-sm"
                                                        />
                                                        {apiConfig.tron_api_urls.length > 1 && (
                                                            <button
                                                                onClick={() => setApiConfig(prev => ({ ...prev, tron_api_urls: prev.tron_api_urls.filter((_, j) => j !== i) }))}
                                                                className="text-gray-400 hover:text-red-500 shrink-0"
                                                            >
                                                                <X className="w-4 h-4" />
                                                            </button>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                        <div>
                                            <div className="flex items-center justify-between mb-1">
                                                <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">TronGrid API Keys</label>
                                                <Button variant="ghost" size="sm" className="text-xs gap-1 h-7"
                                                    onClick={() => setApiConfig(prev => ({ ...prev, tron_api_keys: [...prev.tron_api_keys, ''] }))}
                                                >
                                                    <Plus className="w-3 h-3" /> 添加
                                                </Button>
                                            </div>
                                            <div className="space-y-2">
                                                {apiConfig.tron_api_keys.map((key, i) => (
                                                    <div key={i} className="flex gap-2 items-center">
                                                        <Input
                                                            type="password"
                                                            placeholder="xxxxxxxx-xxxx-xxxx-xxxx"
                                                            value={key}
                                                            onChange={(e) => {
                                                                const updated = [...apiConfig.tron_api_keys]
                                                                updated[i] = e.target.value
                                                                setApiConfig(prev => ({ ...prev, tron_api_keys: updated }))
                                                            }}
                                                        />
                                                        {apiConfig.tron_api_keys.length > 1 && (
                                                            <button
                                                                onClick={() => setApiConfig(prev => ({ ...prev, tron_api_keys: prev.tron_api_keys.filter((_, j) => j !== i) }))}
                                                                className="text-gray-400 hover:text-red-500 shrink-0"
                                                            >
                                                                <X className="w-4 h-4" />
                                                            </button>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                        <div>
                                            <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">USDT 合约地址</label>
                                            <Input
                                                placeholder="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
                                                value={apiConfig.tron_usdt_contract}
                                                onChange={(e) => setApiConfig(prev => ({ ...prev, tron_usdt_contract: e.target.value }))}
                                                className="font-mono text-sm"
                                            />
                                        </div>
                                    </div>
                                </div>

                                {/* TRON 能量租赁 */}
                                <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                    <h3 className="font-semibold text-zinc-900 dark:text-white mb-1">TRON 能量租赁</h3>
                                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">TRC20 转账前自动从第三方平台租赁能量，大幅降低手续费</p>
                                    <div className="space-y-4">
                                        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                            <div>
                                                <div className="text-sm font-medium text-zinc-900 dark:text-white">启用能量租赁</div>
                                                <div className="text-xs text-gray-500 dark:text-gray-400">开启后，每笔 TRC20 转账前会自动检查并租赁能量</div>
                                            </div>
                                            <Toggle
                                                checked={apiConfig.tron_energy_rental_enabled}
                                                onChange={(val) => setApiConfig(prev => ({ ...prev, tron_energy_rental_enabled: val }))}
                                            />
                                        </div>
                                        {apiConfig.tron_energy_rental_enabled && (
                                            <>
                                                <div>
                                                    <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1 block">租赁平台 API 地址</label>
                                                    <Input
                                                        value={apiConfig.tron_energy_rental_api_url}
                                                        onChange={(e) => setApiConfig(prev => ({ ...prev, tron_energy_rental_api_url: e.target.value }))}
                                                        placeholder="https://feee.io"
                                                    />
                                                    <p className="text-xs text-gray-400 mt-1">支持 feee.io 能量租赁平台</p>
                                                </div>
                                                <div>
                                                    <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1 block">API Key</label>
                                                    <Input
                                                        type="password"
                                                        value={apiConfig.tron_energy_rental_api_key}
                                                        onChange={(e) => setApiConfig(prev => ({ ...prev, tron_energy_rental_api_key: e.target.value }))}
                                                        placeholder="平台提供的 API Key"
                                                    />
                                                </div>
                                                <div className="grid grid-cols-2 gap-4">
                                                    <div>
                                                        <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1 block">单价上限 (sun/energy)</label>
                                                        <Input
                                                            type="number"
                                                            value={apiConfig.tron_energy_rental_max_price}
                                                            onChange={(e) => setApiConfig(prev => ({ ...prev, tron_energy_rental_max_price: parseInt(e.target.value) || 420 }))}
                                                        />
                                                        <p className="text-xs text-gray-400 mt-1">当前网络约 420 sun/energy</p>
                                                    </div>
                                                    <div>
                                                        <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300 mb-1 block">租赁时长</label>
                                                        <Select
                                                            value={String(apiConfig.tron_energy_rental_duration)}
                                                            onChange={(v) => setApiConfig(prev => ({ ...prev, tron_energy_rental_duration: parseInt(v) }))}
                                                            options={[
                                                                { value: '600000', label: '10 分钟' },
                                                                { value: '1800000', label: '30 分钟' },
                                                                { value: '3600000', label: '1 小时' },
                                                                { value: '86400000', label: '24 小时' },
                                                            ]}
                                                        />
                                                    </div>
                                                </div>
                                            </>
                                        )}
                                        <div className="p-3 bg-blue-50 dark:bg-blue-900/10 rounded-lg">
                                            <p className="text-xs text-blue-600 dark:text-blue-400">
                                                {apiConfig.tron_energy_rental_enabled
                                                    ? 'USDT 转账需要约 65,000 能量。租赁可将每笔手续费从 30-65 TRX 降至约 3-5 TRX。'
                                                    : '未启用租赁时，TRC20 转账将直接燃烧 TRX 支付能量费用（约 30-65 TRX/笔）。'}
                                            </p>
                                        </div>
                                    </div>
                                </div>

                                {/* 保存按钮 */}
                                <div className="flex justify-end">
                                    <Button variant="primary" className="gap-2" onClick={handleSaveApiConfig} disabled={isSavingApiConfig}>
                                        {isSavingApiConfig && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                                        保存 API 配置
                                    </Button>
                                </div>
                            </div>
                        )}
                    </TabsContent>
                )}

                {/* ─── Tab: 账户安全 ─── */}
                <TabsContent value="security">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* 修改密码 */}
                        <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                            <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">修改密码</h3>
                            <div className="space-y-4">
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">原密码</label>
                                    <Input type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} placeholder="输入当前密码" />
                                </div>
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">新密码</label>
                                    <Input type="password" value={changePwd} onChange={(e) => setChangePwd(e.target.value)} placeholder="至少 6 位" />
                                </div>
                                <div>
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">确认新密码</label>
                                    <Input type="password" value={confirmPwd} onChange={(e) => setConfirmPwd(e.target.value)} placeholder="再次输入新密码" />
                                </div>
                                <Button variant="primary" className="w-full gap-2" onClick={handleChangePassword} disabled={isChangingPwd}>
                                    {isChangingPwd ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                                    修改密码
                                </Button>
                            </div>
                        </div>

                        {/* 两步验证 — 仅在系统启用"强制两步验证"时显示 */}
                        {sysRequire2FA && (
                            <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">两步验证 (2FA)</h3>
                                <div className="space-y-4">
                                    <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                        <div>
                                            <div className="text-sm font-medium text-zinc-900 dark:text-white">当前状态</div>
                                            <div className="text-xs text-gray-500 dark:text-gray-400">
                                                {user?.totp_enabled ? '已启用 Google Authenticator' : '未启用两步验证'}
                                            </div>
                                        </div>
                                        <Badge variant={user?.totp_enabled ? 'success' : 'warning'}>
                                            {user?.totp_enabled ? '已启用' : '未启用'}
                                        </Badge>
                                    </div>
                                    <p className="text-sm text-gray-500 dark:text-gray-400">
                                        两步验证为您的账户增加了额外的安全保障。启用后，登录时需要输入 Google Authenticator 生成的动态验证码。
                                    </p>
                                    {user?.totp_enabled ? (
                                        <Button
                                            variant="outline"
                                            className="w-full text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-500/10"
                                            onClick={() => { setDisableCode(''); setDisableTwoFAModalOpen(true) }}
                                        >
                                            关闭两步验证
                                        </Button>
                                    ) : (
                                        <Button variant="primary" className="w-full gap-2" onClick={handleSetup2FA} disabled={isSettingUp2FA}>
                                            {isSettingUp2FA ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Shield className="w-4 h-4" />}
                                            设置两步验证
                                        </Button>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* Google 邮箱绑定 — 仅在系统启用"Google 登录"时显示 */}
                        {sysEnableGoogle && (
                            <div className="elegant-card p-6 bg-white dark:bg-[#181a20] border-gray-100 dark:border-[#2a2d35]">
                                <h3 className="font-semibold text-zinc-900 dark:text-white mb-4">Google 邮箱绑定</h3>
                                <div className="space-y-4">
                                    <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                                        <div>
                                            <div className="text-sm font-medium text-zinc-900 dark:text-white">当前状态</div>
                                            <div className="text-xs text-gray-500 dark:text-gray-400">
                                                {user?.google_email ? user.google_email : '未绑定 Google 邮箱'}
                                            </div>
                                        </div>
                                        <Badge variant={user?.google_email ? 'success' : 'warning'}>
                                            {user?.google_email ? '已绑定' : '未绑定'}
                                        </Badge>
                                    </div>
                                    <p className="text-sm text-gray-500 dark:text-gray-400">
                                        绑定 Google 邮箱后，您可以通过 Google 一键登录。解绑时需要输入两步验证码确认。
                                    </p>
                                    {user?.google_email ? (
                                        <Button
                                            variant="outline"
                                            className="w-full text-red-600 dark:text-red-400 border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-500/10"
                                            onClick={() => { setUnbindGoogleCode(''); setUnbindGoogleModalOpen(true) }}
                                            disabled={!user?.totp_enabled}
                                        >
                                            {!user?.totp_enabled ? '请先启用两步验证后再解绑' : '解绑 Google 邮箱'}
                                        </Button>
                                    ) : (
                                        <Button variant="primary" className="w-full gap-2" onClick={() => { setBindGoogleEmail(''); setBindGoogleModalOpen(true) }}>
                                            绑定 Google 邮箱
                                        </Button>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </TabsContent>

                {/* ─── Tab 4: 审计日志 ─── */}
                {hasPermission('audit_logs') && (
                    <TabsContent value="logs">
                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
                            <div className="relative">
                                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                                <Input
                                    className="pl-9 w-[280px]"
                                    placeholder="搜索操作人或操作内容..."
                                    value={logSearch}
                                    onChange={(e) => setLogSearch(e.target.value)}
                                    onKeyDown={(e) => e.key === 'Enter' && handleLogSearch()}
                                />
                                {logSearch && (
                                    <button
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                                        onClick={() => { setLogSearch(''); fetchAuditLogs(1) }}
                                    >
                                        <X className="w-3.5 h-3.5" />
                                    </button>
                                )}
                            </div>
                            <Button variant="outline" onClick={handleLogSearch}>
                                搜索
                            </Button>
                        </div>
                        {loadingLogs ? (
                            <div className="flex justify-center py-12">
                                <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                            </div>
                        ) : (
                            <>
                                <DataTable columns={logColumns} data={auditLogs} />
                                {logTotal > 20 && (
                                    <div className="flex items-center justify-between mt-4">
                                        <span className="text-sm text-gray-500">共 {logTotal} 条</span>
                                        <div className="flex items-center gap-2">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                disabled={logPage <= 1}
                                                onClick={() => fetchAuditLogs(logPage - 1, logSearch)}
                                            >
                                                <ChevronLeft className="w-4 h-4" />
                                            </Button>
                                            <span className="text-sm text-gray-500">{logPage} / {logTotalPages}</span>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                disabled={logPage >= logTotalPages}
                                                onClick={() => fetchAuditLogs(logPage + 1, logSearch)}
                                            >
                                                <ChevronRight className="w-4 h-4" />
                                            </Button>
                                        </div>
                                    </div>
                                )}
                            </>
                        )}
                    </TabsContent>
                )}

                {/* ─── Tab: 权限管理 ─── */}
                {isSuperAdmin && (
                    <TabsContent value="permissions">
                        {loadingPerms ? (
                            <div className="flex items-center justify-center py-20">
                                <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                            </div>
                        ) : permConfig ? (
                            <div className="space-y-6">
                                <div className="bg-white dark:bg-[#181a20] rounded-2xl border border-gray-100 dark:border-[#23262f] shadow-sm overflow-hidden">
                                    <div className="px-6 py-4 border-b border-gray-100 dark:border-[#23262f] flex items-center justify-between">
                                        <div>
                                            <h3 className="text-base font-semibold text-zinc-900 dark:text-white">角色权限配置</h3>
                                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">配置每个角色可以访问的功能模块，超级管理员始终拥有全部权限</p>
                                        </div>
                                        <div className="flex gap-2">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => setEditedPerms(JSON.parse(JSON.stringify(permConfig.defaults)))}
                                            >
                                                恢复默认
                                            </Button>
                                            <Button
                                                size="sm"
                                                disabled={savingPerms}
                                                onClick={async () => {
                                                    setSavingPerms(true)
                                                    try {
                                                        const { data } = await settingsApi.updatePermissions({
                                                            operator: editedPerms.operator || [],
                                                            signer: editedPerms.signer || [],
                                                            viewer: editedPerms.viewer || [],
                                                        })
                                                        setPermConfig(data)
                                                        setEditedPerms(JSON.parse(JSON.stringify(data.current)))
                                                        toast.success('权限配置已保存')
                                                    } catch {
                                                        toast.error('保存失败')
                                                    } finally {
                                                        setSavingPerms(false)
                                                    }
                                                }}
                                            >
                                                {savingPerms ? <Loader2 className="w-4 h-4 animate-spin" /> : '保存配置'}
                                            </Button>
                                        </div>
                                    </div>
                                    <div className="overflow-x-auto">
                                        <table className="w-full text-sm">
                                            <thead>
                                                <tr className="border-b border-gray-100 dark:border-[#23262f]">
                                                    <th className="text-left px-6 py-3.5 font-medium text-gray-500 dark:text-gray-400 w-[200px]">功能模块</th>
                                                    <th className="text-center px-4 py-3.5 font-medium text-gray-500 dark:text-gray-400">操作员</th>
                                                    <th className="text-center px-4 py-3.5 font-medium text-gray-500 dark:text-gray-400">签名者</th>
                                                    <th className="text-center px-4 py-3.5 font-medium text-gray-500 dark:text-gray-400">查看者</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {/* 页面模块 */}
                                                <tr>
                                                    <td colSpan={4} className="px-6 py-2.5 bg-gray-50 dark:bg-[#13151a] text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                                                        页面模块
                                                    </td>
                                                </tr>
                                                {permConfig.all_modules.filter(mod => !mod.key.startsWith('notif_') && ['dashboard','deposits','collections','addresses','payouts','multisig'].includes(mod.key)).map(mod => (
                                                    <tr key={mod.key} className="border-b border-gray-50 dark:border-[#1e2028] hover:bg-gray-50/50 dark:hover:bg-[#1a1c22]">
                                                        <td className="px-6 py-3 text-zinc-800 dark:text-gray-200 font-medium">{mod.label}</td>
                                                        {(['operator', 'signer', 'viewer'] as const).map(role => (
                                                            <td key={role} className="text-center px-4 py-3">
                                                                <Toggle
                                                                    checked={(editedPerms[role] || []).includes(mod.key)}
                                                                    onChange={(val) => {
                                                                        setEditedPerms(prev => {
                                                                            const list = [...(prev[role] || [])]
                                                                            if (val) {
                                                                                if (!list.includes(mod.key)) list.push(mod.key)
                                                                            } else {
                                                                                const idx = list.indexOf(mod.key)
                                                                                if (idx >= 0) list.splice(idx, 1)
                                                                            }
                                                                            return { ...prev, [role]: list }
                                                                        })
                                                                    }}
                                                                />
                                                            </td>
                                                        ))}
                                                    </tr>
                                                ))}
                                                {/* 设置子模块 */}
                                                <tr>
                                                    <td colSpan={4} className="px-6 py-2.5 bg-gray-50 dark:bg-[#13151a] text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                                                        设置功能
                                                    </td>
                                                </tr>
                                                {permConfig.all_modules.filter(mod => !mod.key.startsWith('notif_') && !['dashboard','deposits','collections','addresses','payouts','multisig'].includes(mod.key)).map(mod => (
                                                    <tr key={mod.key} className="border-b border-gray-50 dark:border-[#1e2028] hover:bg-gray-50/50 dark:hover:bg-[#1a1c22]">
                                                        <td className="px-6 py-3 text-zinc-800 dark:text-gray-200 font-medium">{mod.label}</td>
                                                        {(['operator', 'signer', 'viewer'] as const).map(role => (
                                                            <td key={role} className="text-center px-4 py-3">
                                                                <Toggle
                                                                    checked={(editedPerms[role] || []).includes(mod.key)}
                                                                    onChange={(val) => {
                                                                        setEditedPerms(prev => {
                                                                            const list = [...(prev[role] || [])]
                                                                            if (val) {
                                                                                if (!list.includes(mod.key)) list.push(mod.key)
                                                                            } else {
                                                                                const idx = list.indexOf(mod.key)
                                                                                if (idx >= 0) list.splice(idx, 1)
                                                                            }
                                                                            return { ...prev, [role]: list }
                                                                        })
                                                                    }}
                                                                />
                                                            </td>
                                                        ))}
                                                    </tr>
                                                ))}
                                                {/* 通知权限 */}
                                                <tr>
                                                    <td colSpan={4} className="px-6 py-2.5 bg-gray-50 dark:bg-[#13151a] text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
                                                        通知权限
                                                    </td>
                                                </tr>
                                                {permConfig.all_modules.filter(mod => mod.key.startsWith('notif_')).map(mod => (
                                                    <tr key={mod.key} className="border-b border-gray-50 dark:border-[#1e2028] hover:bg-gray-50/50 dark:hover:bg-[#1a1c22]">
                                                        <td className="px-6 py-3 text-zinc-800 dark:text-gray-200 font-medium">{mod.label}</td>
                                                        {(['operator', 'signer', 'viewer'] as const).map(role => (
                                                            <td key={role} className="text-center px-4 py-3">
                                                                <Toggle
                                                                    checked={(editedPerms[role] || []).includes(mod.key)}
                                                                    onChange={(val) => {
                                                                        setEditedPerms(prev => {
                                                                            const list = [...(prev[role] || [])]
                                                                            if (val) {
                                                                                if (!list.includes(mod.key)) list.push(mod.key)
                                                                            } else {
                                                                                const idx = list.indexOf(mod.key)
                                                                                if (idx >= 0) list.splice(idx, 1)
                                                                            }
                                                                            return { ...prev, [role]: list }
                                                                        })
                                                                    }}
                                                                />
                                                            </td>
                                                        ))}
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>

                                {/* 说明 */}
                                <div className="bg-blue-50/50 dark:bg-blue-500/5 rounded-2xl border border-blue-100 dark:border-blue-500/10 p-5">
                                    <h4 className="text-sm font-semibold text-blue-800 dark:text-blue-300 mb-2 flex items-center gap-2">
                                        <Shield className="w-4 h-4" /> 权限说明
                                    </h4>
                                    <ul className="text-xs text-blue-700/80 dark:text-blue-300/60 space-y-1.5">
                                        <li>- 超级管理员始终拥有全部权限，不在此配置范围内</li>
                                        <li>- 修改权限后，受影响的用户需要重新登录才能生效</li>
                                        <li>- "账户安全"设置（修改密码、2FA）对所有角色始终可见</li>
                                        <li>- 页面模块控制导航栏菜单可见性，设置功能控制设置页面中的 Tab 可见性</li>
                                    </ul>
                                </div>
                            </div>
                        ) : null}
                    </TabsContent>
                )}
            </Tabs>

            {/* ─── Add Admin Modal ─── */}
            <Modal isOpen={addModalOpen} onClose={() => setAddModalOpen(false)} title="添加管理员">
                <div className="flex flex-col gap-5">
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">用户名</label>
                        <Input placeholder="输入用户名" className="h-11" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} />
                    </div>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">初始密码</label>
                        <Input type="password" placeholder="至少 6 位" className="h-11" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
                    </div>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">角色</label>
                        <Select
                            value={newRole}
                            onChange={setNewRole}
                            options={[
                                { value: 'operator', label: '操作员' },
                                { value: 'signer', label: '签名者' },
                                { value: 'viewer', label: '查看者' },
                            ]}
                        />
                    </div>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">
                            Google 邮箱 <span className="text-gray-400">(可选)</span>
                        </label>
                        <Input placeholder="用于 Google 一键登录" className="h-11" value={newGoogleEmail} onChange={(e) => setNewGoogleEmail(e.target.value)} />
                    </div>
                    {(newRole === 'signer' || newRole === 'super_admin') && (
                        <>
                            <div>
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">
                                    BSC 签名地址 <span className="text-gray-400">(可选)</span>
                                </label>
                                <Input placeholder="0x..." className="h-11" value={newSignerBsc} onChange={(e) => setNewSignerBsc(e.target.value)} />
                            </div>
                            <div>
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">
                                    TRON 签名地址 <span className="text-gray-400">(可选)</span>
                                </label>
                                <Input placeholder="T..." className="h-11" value={newSignerTron} onChange={(e) => setNewSignerTron(e.target.value)} />
                            </div>
                        </>
                    )}
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">
                            TG 用户名 <span className="text-gray-400">(可选，用于自动绑定通知)</span>
                        </label>
                        <Input placeholder="Telegram 用户名，不带 @" className="h-11" value={newTgUsername} onChange={(e) => setNewTgUsername(e.target.value)} />
                    </div>
                    <Button variant="primary" size="lg" className="w-full mt-2 gap-2" onClick={handleAddAdmin} disabled={isAdding}>
                        {isAdding ? <><Loader2 className="w-4 h-4 animate-spin" /> 添加中...</> : '确认添加'}
                    </Button>
                </div>
            </Modal>

            {/* ─── Edit Admin Modal ─── */}
            <Modal isOpen={editModalOpen} onClose={() => setEditModalOpen(false)} title="编辑管理员">
                {editingAdmin && (
                    <div className="flex flex-col gap-5">
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">用户名</label>
                            <Input className="h-11 bg-gray-50 dark:bg-[#1c1f26]" value={editingAdmin.username} disabled />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">
                                角色
                                {editingAdmin.id === user?.id && <span className="text-xs text-amber-500 ml-2">不能修改自己的角色</span>}
                            </label>
                            <Select
                                value={editingAdmin.role}
                                onChange={(val) => setEditingAdmin({ ...editingAdmin, role: val })}
                                options={[
                                    { value: 'super_admin', label: '超级管理员' },
                                    { value: 'operator', label: '操作员' },
                                    { value: 'signer', label: '签名者' },
                                    { value: 'viewer', label: '查看者' },
                                ]}
                                disabled={editingAdmin.id === user?.id}
                            />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">Google 邮箱</label>
                            <Input
                                className="h-11"
                                placeholder="用于 Google 一键登录"
                                value={editingAdmin.google_email || ''}
                                onChange={(e) => setEditingAdmin({ ...editingAdmin, google_email: e.target.value || null })}
                            />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">BSC 签名地址</label>
                            <Input
                                className="h-11"
                                placeholder="0x..."
                                value={editingAdmin.signer_address_bsc || ''}
                                onChange={(e) => setEditingAdmin({ ...editingAdmin, signer_address_bsc: e.target.value || null })}
                            />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">TRON 签名地址</label>
                            <Input
                                className="h-11"
                                placeholder="T..."
                                value={editingAdmin.signer_address_tron || ''}
                                onChange={(e) => setEditingAdmin({ ...editingAdmin, signer_address_tron: e.target.value || null })}
                            />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">TG 用户名</label>
                            <Input
                                className="h-11"
                                placeholder="Telegram 用户名，不带 @"
                                value={editingAdmin.tg_username || ''}
                                onChange={(e) => setEditingAdmin({ ...editingAdmin, tg_username: e.target.value || null })}
                            />
                            {editingAdmin.tg_chat_id && (
                                <p className="text-xs text-emerald-500 mt-1">已绑定 (Chat ID: {editingAdmin.tg_chat_id})</p>
                            )}
                            {editingAdmin.tg_username && !editingAdmin.tg_chat_id && (
                                <p className="text-xs text-amber-500 mt-1">待绑定 — 请让该管理员给 Bot 发送 /start</p>
                            )}
                        </div>
                        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-[#1c1f26] rounded-xl">
                            <div>
                                <div className="text-sm font-medium text-zinc-900 dark:text-white">账户状态</div>
                                <div className="text-xs text-gray-500 dark:text-gray-400">
                                    {editingAdmin.id === user?.id
                                        ? '不能禁用自己的账户'
                                        : editingAdmin.is_active ? '当前已启用' : '当前已禁用'}
                                </div>
                            </div>
                            <Toggle
                                checked={editingAdmin.is_active}
                                onChange={(val) => setEditingAdmin({ ...editingAdmin, is_active: val })}
                                disabled={editingAdmin.id === user?.id}
                            />
                        </div>
                        <div className="flex gap-3">
                            <Button variant="outline" className="flex-1" onClick={() => setEditModalOpen(false)}>取消</Button>
                            <Button variant="primary" className="flex-1 gap-2" onClick={handleSaveEdit} disabled={isSavingEdit}>
                                {isSavingEdit ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                                保存修改
                            </Button>
                        </div>
                    </div>
                )}
            </Modal>

            {/* ─── Reset Password Modal ─── */}
            <Modal isOpen={resetPwdModalOpen} onClose={() => setResetPwdModalOpen(false)} title={`重置密码 — ${resetPwdAdmin?.username}`}>
                <div className="flex flex-col gap-5">
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">新密码</label>
                        <Input type="password" placeholder="至少 6 位" className="h-11" value={newAdminPassword} onChange={(e) => setNewAdminPassword(e.target.value)} />
                    </div>
                    <Button variant="primary" size="lg" className="w-full gap-2" onClick={handleResetPassword} disabled={isResettingPwd}>
                        {isResettingPwd ? <><Loader2 className="w-4 h-4 animate-spin" /> 重置中...</> : '确认重置'}
                    </Button>
                </div>
            </Modal>

            {/* ─── 2FA Setup Modal ─── */}
            <Modal isOpen={twoFAModalOpen} onClose={() => setTwoFAModalOpen(false)} title="设置两步验证">
                <div className="flex flex-col gap-5">
                    <p className="text-sm text-gray-500 dark:text-gray-400">
                        使用 Google Authenticator 或其他兼容应用扫描以下二维码，然后输入生成的 6 位验证码完成绑定。
                    </p>
                    {twoFAQrUri && (
                        <div className="flex justify-center p-4 bg-white rounded-xl">
                            <QRCodeSVG value={twoFAQrUri} size={200} />
                        </div>
                    )}
                    <div className="flex items-center gap-2 p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg">
                        <code className="flex-1 text-xs font-mono text-zinc-900 dark:text-white break-all">{twoFASecret}</code>
                        <button onClick={handleCopySecret} className="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded transition-colors">
                            {copied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4 text-gray-400" />}
                        </button>
                    </div>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">验证码</label>
                        <Input
                            placeholder="输入 6 位验证码"
                            className="h-11 text-center text-lg tracking-[0.5em] font-mono"
                            maxLength={6}
                            value={twoFACode}
                            onChange={(e) => setTwoFACode(e.target.value.replace(/\D/g, ''))}
                        />
                    </div>
                    <Button variant="primary" size="lg" className="w-full gap-2" onClick={handleEnable2FA} disabled={isEnabling2FA}>
                        {isEnabling2FA ? <><Loader2 className="w-4 h-4 animate-spin" /> 验证中...</> : '确认启用'}
                    </Button>
                </div>
            </Modal>

            {/* ─── Disable 2FA Modal ─── */}
            <Modal isOpen={disableTwoFAModalOpen} onClose={() => setDisableTwoFAModalOpen(false)} title="关闭两步验证">
                <div className="flex flex-col gap-5">
                    <p className="text-sm text-amber-600 dark:text-amber-400">
                        关闭两步验证会降低您的账户安全性。请输入当前验证码确认操作。
                    </p>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">验证码</label>
                        <Input
                            placeholder="输入 6 位验证码"
                            className="h-11 text-center text-lg tracking-[0.5em] font-mono"
                            maxLength={6}
                            value={disableCode}
                            onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, ''))}
                        />
                    </div>
                    <Button
                        variant="danger"
                        size="lg"
                        className="w-full gap-2"
                        onClick={handleDisable2FA}
                        disabled={isDisabling2FA}
                    >
                        {isDisabling2FA ? <><Loader2 className="w-4 h-4 animate-spin" /> 关闭中...</> : '确认关闭'}
                    </Button>
                </div>
            </Modal>

            {/* ─── Bind Google Email Modal ─── */}
            <Modal isOpen={bindGoogleModalOpen} onClose={() => setBindGoogleModalOpen(false)} title="绑定 Google 邮箱">
                <div className="flex flex-col gap-5">
                    <p className="text-sm text-gray-500 dark:text-gray-400">
                        输入您的 Google 邮箱地址。绑定后可使用 Google 一键登录。
                    </p>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">Google 邮箱</label>
                        <Input
                            placeholder="example@gmail.com"
                            className="h-11"
                            value={bindGoogleEmail}
                            onChange={(e) => setBindGoogleEmail(e.target.value)}
                        />
                    </div>
                    <Button variant="primary" size="lg" className="w-full gap-2" onClick={handleBindGoogleEmail} disabled={isBindingGoogle}>
                        {isBindingGoogle ? <><Loader2 className="w-4 h-4 animate-spin" /> 绑定中...</> : '确认绑定'}
                    </Button>
                </div>
            </Modal>

            {/* ─── Unbind Google Email Modal ─── */}
            <Modal isOpen={unbindGoogleModalOpen} onClose={() => setUnbindGoogleModalOpen(false)} title="解绑 Google 邮箱">
                <div className="flex flex-col gap-5">
                    <p className="text-sm text-amber-600 dark:text-amber-400">
                        解绑后将无法使用 Google 一键登录。请输入两步验证码确认操作。
                    </p>
                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">验证码</label>
                        <Input
                            placeholder="输入 6 位验证码"
                            className="h-11 text-center text-lg tracking-[0.5em] font-mono"
                            maxLength={6}
                            value={unbindGoogleCode}
                            onChange={(e) => setUnbindGoogleCode(e.target.value.replace(/\D/g, ''))}
                        />
                    </div>
                    <Button
                        variant="danger"
                        size="lg"
                        className="w-full gap-2"
                        onClick={handleUnbindGoogleEmail}
                        disabled={isUnbindingGoogle}
                    >
                        {isUnbindingGoogle ? <><Loader2 className="w-4 h-4 animate-spin" /> 解绑中...</> : '确认解绑'}
                    </Button>
                </div>
            </Modal>

            {/* ─── Edit Wallet Modal ─── */}
            {/* 编辑钱包 Modal */}
            <Modal isOpen={!!editingWallet} onClose={() => setEditingWallet(null)} title={editingWallet?.label || '编辑钱包'}>
                {editingWallet && (
                    <div className="flex flex-col gap-5">
                        <div className="flex gap-3">
                            <div className="flex-1">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">链</label>
                                <Input className="h-11 bg-gray-50 dark:bg-[#1c1f26]" value={editingWallet.chain} disabled />
                            </div>
                            <div className="flex-1">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">类型</label>
                                <Input className="h-11 bg-gray-50 dark:bg-[#1c1f26]" value={
                                    editingWallet.type === 'gas' ? 'Gas' : editingWallet.type === 'collection' ? '归集' : '打款'
                                } disabled />
                            </div>
                        </div>
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">
                                钱包地址（只读）
                            </label>
                            <Input className="h-11 font-mono bg-gray-50 dark:bg-[#1c1f26]" value={editingWallet.address || ''} disabled />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">标签</label>
                            <Input
                                className="h-11"
                                value={editingWallet.label || ''}
                                onChange={(e) => setEditingWallet({ ...editingWallet, label: e.target.value })}
                            />
                        </div>
                        {editingWallet.is_multisig && editingWallet.chain === 'TRON' && (
                            <div>
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">中转钱包</label>
                                <Select
                                    value={editingWallet.relay_wallet_id != null ? String(editingWallet.relay_wallet_id) : ''}
                                    onChange={(val) => setEditingWallet({
                                        ...editingWallet,
                                        relay_wallet_id: val ? Number(val) : null,
                                    })}
                                    options={[
                                        { label: '无（不使用中转钱包）', value: '' },
                                        ...wallets
                                            .filter(w => w.chain === 'TRON' && w.type === 'payout' && !w.is_multisig)
                                            .map(w => ({
                                                label: `${w.label || '中转钱包'} — ${w.address ? w.address.slice(0, 8) + '...' + w.address.slice(-4) : ''}`,
                                                value: String(w.id),
                                            })),
                                    ]}
                                />
                                <p className="text-xs text-gray-400 dark:text-gray-500 mt-1.5">TRON 多签打款时，资金先归集到中转钱包再转出</p>
                            </div>
                        )}
                        <Button variant="primary" size="lg" className="w-full mt-2 gap-2" onClick={handleSaveWallet} disabled={isSavingWallet}>
                            {isSavingWallet ? <><Loader2 className="w-4 h-4 animate-spin" /> 保存中...</> : '保存'}
                        </Button>
                    </div>
                )}
            </Modal>

            {/* 新建钱包 Modal */}
            <Modal isOpen={showCreateWallet} onClose={() => setShowCreateWallet(false)} title="新建钱包">
                <div className="flex flex-col gap-5">
                    <div className="flex gap-3">
                        <div className="flex-1">
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">链</label>
                            <Select
                                value={newWallet.chain}
                                onChange={(val) => setNewWallet({ ...newWallet, chain: val })}
                                options={[
                                    { label: 'BSC', value: 'BSC' },
                                    { label: 'TRON', value: 'TRON' },
                                ]}
                            />
                        </div>
                        <div className="flex-1">
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">类型</label>
                            <Select
                                value={newWallet.type}
                                onChange={(val) => setNewWallet({ ...newWallet, type: val })}
                                options={[
                                    { label: 'Gas 钱包', value: 'gas' },
                                    { label: '归集钱包', value: 'collection' },
                                    { label: '打款钱包', value: 'payout' },
                                ]}
                            />
                        </div>
                    </div>

                    {newWallet.type === 'gas' ? (
                        <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg">
                            <p className="text-sm text-gray-600 dark:text-gray-400">
                                地址和派生索引将由 HD 钱包自动分配。创建后可导出私钥，导入 MetaMask 充值 BNB/TRX 作为手续费储备。
                            </p>
                        </div>
                    ) : (
                        <div>
                            <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">Safe 多签地址</label>
                            <Input
                                placeholder={newWallet.chain === 'BSC' ? '0x...' : 'T...'}
                                className="h-11 font-mono"
                                value={newWallet.address}
                                onChange={(e) => setNewWallet({ ...newWallet, address: e.target.value })}
                            />
                        </div>
                    )}

                    <div>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 block">标签（可选）</label>
                        <Input
                            placeholder="如：BSC Gas #0"
                            className="h-11"
                            value={newWallet.label}
                            onChange={(e) => setNewWallet({ ...newWallet, label: e.target.value })}
                        />
                    </div>

                    <Button variant="primary" size="lg" className="w-full mt-2 gap-2" onClick={handleCreateWallet} disabled={isCreatingWallet}>
                        {isCreatingWallet ? <><Loader2 className="w-4 h-4 animate-spin" /> 创建中...</> : '创建'}
                    </Button>
                </div>
            </Modal>

            {/* 删除确认 Modal */}
            <Modal isOpen={!!deletingWalletId} onClose={() => setDeletingWalletId(null)} title="确认删除">
                <div className="flex flex-col gap-5">
                    <p className="text-sm text-gray-600 dark:text-gray-400">确定要删除这个钱包吗？此操作不可恢复。</p>
                    <div className="flex gap-3">
                        <Button variant="outline" className="flex-1" onClick={() => setDeletingWalletId(null)}>取消</Button>
                        <Button variant="primary" className="flex-1 bg-red-600 hover:bg-red-500" onClick={() => deletingWalletId && handleDeleteWallet(deletingWalletId)}>
                            确认删除
                        </Button>
                    </div>
                </div>
            </Modal>

            {/* 地址二维码 Modal */}
            <Modal isOpen={!!qrWallet} onClose={() => setQrWallet(null)} title="地址二维码">
                {qrWallet && (
                    <div className="flex flex-col items-center gap-4 py-2">
                        <p className="text-sm text-gray-600 dark:text-gray-400">{qrWallet.label}</p>
                        <div className="bg-white p-4 rounded-xl">
                            <QRCodeSVG value={qrWallet.address} size={200} />
                        </div>
                        <div className="text-xs font-mono text-zinc-700 dark:text-gray-300 break-all text-center px-4 cursor-pointer hover:text-blue-500 transition-colors"
                            onClick={() => { navigator.clipboard.writeText(qrWallet.address); toast.success('地址已复制') }}>
                            {qrWallet.address}
                        </div>
                    </div>
                )}
            </Modal>

            {/* 导出私钥 Modal */}
            <Modal isOpen={!!exportedKey} onClose={() => { setExportedKey(null); setShowPrivateKey(false) }} title="Gas 钱包私钥">
                {exportedKey && (
                    <div className="flex flex-col gap-4">
                        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                            <p className="text-sm text-red-600 dark:text-red-400 font-medium">
                                请妥善保管私钥，不要泄露给任何人！
                            </p>
                        </div>
                        <div>
                            <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">地址</label>
                            <div className="text-sm font-mono text-zinc-700 dark:text-gray-300 break-all bg-gray-50 dark:bg-[#1c1f26] p-3 rounded-lg">
                                {exportedKey.address}
                            </div>
                        </div>
                        <div>
                            <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">私钥</label>
                            <div className="relative">
                                <div className="text-sm font-mono text-zinc-700 dark:text-gray-300 break-all bg-gray-50 dark:bg-[#1c1f26] p-3 pr-10 rounded-lg select-all">
                                    {showPrivateKey ? exportedKey.private_key : '••••••••••••••••••••••••••••••••••••••••••••••••••••••••••••••••'}
                                </div>
                                <button
                                    type="button"
                                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
                                    onClick={() => setShowPrivateKey(!showPrivateKey)}
                                    title={showPrivateKey ? '隐藏私钥' : '显示私钥'}
                                >
                                    {showPrivateKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                </button>
                            </div>
                        </div>
                        <Button variant="outline" className="w-full gap-2" onClick={() => {
                            navigator.clipboard.writeText(exportedKey.private_key)
                            toast.success('私钥已复制到剪贴板')
                        }}>
                            <Copy className="w-4 h-4" /> 复制私钥
                        </Button>
                    </div>
                )}
            </Modal>

            {/* 创建多签钱包 Modal */}
            <Modal isOpen={showMultisigCreate} onClose={() => setShowMultisigCreate(false)} title={`创建多签钱包 (${multisigStep + 1}/3)`}>
                <div className="flex flex-col gap-4">
                    {multisigStep === 0 && (
                        <>
                            <div>
                                <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">链</label>
                                <Select value={multisigForm.chain} onChange={(v) => setMultisigForm(f => ({ ...f, chain: v, owners: [] }))}
                                    options={[{ value: 'BSC', label: 'BSC' }, { value: 'TRON', label: 'TRON' }]} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">类型</label>
                                <Select value={multisigForm.type} onChange={(v) => setMultisigForm(f => ({ ...f, type: v }))}
                                    options={[{ value: 'collection', label: '归集钱包' }, { value: 'payout', label: '打款钱包' }]} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">标签（可选）</label>
                                <Input value={multisigForm.label} onChange={(e) => setMultisigForm(f => ({ ...f, label: e.target.value }))} placeholder="如: BSC 归集多签" />
                            </div>
                            <Button variant="primary" className="w-full" onClick={async () => {
                                try {
                                    const res = await settingsApi.getSigners(multisigForm.chain)
                                    setAvailableSigners(res.data)
                                } catch { setAvailableSigners([]) }
                                setMultisigStep(1)
                            }}>下一步：配置签名人</Button>
                        </>
                    )}

                    {multisigStep === 1 && (
                        <>
                            <div>
                                <label className="text-xs text-gray-500 dark:text-gray-400 mb-2 block">签名人列表</label>
                                {/* 从管理员添加 */}
                                {availableSigners.length > 0 && (
                                    <div className="mb-3">
                                        <p className="text-xs text-gray-400 mb-1">从系统管理员添加：</p>
                                        <div className="flex flex-wrap gap-2">
                                            {availableSigners.filter(s => s.address).map(s => {
                                                const already = multisigForm.owners.some(o => o.admin_id === s.admin_id)
                                                return (
                                                    <button key={s.admin_id} type="button"
                                                        className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${already
                                                            ? 'bg-blue-50 dark:bg-blue-900/30 border-blue-300 dark:border-blue-600 text-blue-600 dark:text-blue-400'
                                                            : 'bg-gray-50 dark:bg-[#1c1f26] border-gray-200 dark:border-[#2a2d35] text-gray-600 dark:text-gray-400 hover:border-blue-300'
                                                        }`}
                                                        onClick={() => {
                                                            if (already) {
                                                                setMultisigForm(f => ({ ...f, owners: f.owners.filter(o => o.admin_id !== s.admin_id) }))
                                                            } else {
                                                                setMultisigForm(f => ({ ...f, owners: [...f.owners, { admin_id: s.admin_id, display: `${s.username} (${s.address?.slice(0, 8)}...)` }] }))
                                                            }
                                                        }}>
                                                        {already ? <Check className="w-3 h-3 inline mr-1" /> : null}
                                                        {s.username}
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    </div>
                                )}

                                {/* 手动添加 */}
                                <div className="flex gap-2 mb-3">
                                    <Input className="flex-1 text-xs" placeholder="手动输入地址" value={manualAddress}
                                        onChange={(e) => setManualAddress(e.target.value)} />
                                    <Button variant="outline" size="sm" onClick={() => {
                                        const addr = manualAddress.trim()
                                        if (!addr) return
                                        if (multisigForm.chain === 'BSC' && !/^0x[0-9a-fA-F]{40}$/.test(addr)) {
                                            toast.error('请输入有效的 BSC 地址 (0x...)'); return
                                        }
                                        if (multisigForm.chain === 'TRON' && !/^T[1-9A-HJ-NP-Za-km-z]{33}$/.test(addr)) {
                                            toast.error('请输入有效的 TRON 地址 (T...)'); return
                                        }
                                        if (multisigForm.owners.some(o => o.address === addr)) {
                                            toast.error('地址已存在'); return
                                        }
                                        setMultisigForm(f => ({ ...f, owners: [...f.owners, { address: addr, display: addr.slice(0, 10) + '...' }] }))
                                        setManualAddress('')
                                    }}>添加</Button>
                                </div>

                                {/* 已添加列表 */}
                                {multisigForm.owners.length > 0 && (
                                    <div className="space-y-1.5 mb-3">
                                        {multisigForm.owners.map((o, i) => (
                                            <div key={i} className="flex items-center justify-between text-xs bg-gray-50 dark:bg-[#1c1f26] p-2 rounded">
                                                <span className="truncate mr-2 text-zinc-700 dark:text-gray-300">{o.display || o.address}</span>
                                                <button type="button" className="text-red-400 hover:text-red-500" onClick={() => {
                                                    setMultisigForm(f => ({ ...f, owners: f.owners.filter((_, idx) => idx !== i) }))
                                                }}><X className="w-3.5 h-3.5" /></button>
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {/* Threshold */}
                                <div>
                                    <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">确认阈值（需要几个签名）</label>
                                    {multisigForm.owners.length === 0 ? (
                                        <p className="text-xs text-gray-400 bg-gray-50 dark:bg-[#1c1f26] rounded px-3 py-2">请先添加签名人地址</p>
                                    ) : (
                                        <Select value={String(multisigForm.threshold)} onChange={(v) => setMultisigForm(f => ({ ...f, threshold: Number(v) }))}
                                            options={Array.from({ length: multisigForm.owners.length }, (_, i) => ({
                                                value: String(i + 1), label: `${i + 1} / ${multisigForm.owners.length}`
                                            }))} />
                                    )}
                                </div>
                            </div>
                            <div className="flex gap-2">
                                <Button variant="outline" className="flex-1" onClick={() => setMultisigStep(0)}>上一步</Button>
                                <Button variant="primary" className="flex-1" disabled={multisigForm.owners.length < 2}
                                    onClick={() => setMultisigStep(2)}>下一步：确认</Button>
                            </div>
                        </>
                    )}

                    {multisigStep === 2 && (
                        <>
                            <div className="p-3 bg-gray-50 dark:bg-[#1c1f26] rounded-lg text-sm space-y-2">
                                <div className="flex justify-between"><span className="text-gray-500">链</span><span className="font-medium text-zinc-900 dark:text-white">{multisigForm.chain}</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">类型</span><span className="font-medium text-zinc-900 dark:text-white">{multisigForm.type === 'collection' ? '归集钱包' : '打款钱包'}</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">签名人</span><span className="font-medium text-zinc-900 dark:text-white">{multisigForm.owners.length} 个</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">确认阈值</span><span className="font-medium text-zinc-900 dark:text-white">{multisigForm.threshold} / {multisigForm.owners.length}</span></div>
                            </div>

                            {multisigForm.chain === 'TRON' && (
                                <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
                                    <p className="text-xs text-blue-700 dark:text-blue-400">
                                        系统将自动使用 TRX 余额最高的 Gas 钱包部署 TronMultiSig 合约，并自动租赁能量（约 50~100 TRX）。部署完成后合约地址自动生效，无需手动激活。
                                    </p>
                                </div>
                            )}

                            {multisigForm.chain === 'BSC' && (() => {
                                const bscGasWallets = wallets.filter(w => w.type === 'gas' && w.chain === 'BSC' && w.address)
                                return (
                                    <div className="space-y-2">
                                        <label className="text-xs text-gray-500 dark:text-gray-400 block">Gas 钱包（支付部署费用，约 0.01 BNB）</label>
                                        {bscGasWallets.length === 0 ? (
                                            <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                                                <p className="text-xs text-red-600 dark:text-red-400">未找到 BSC Gas 钱包，请先在钱包管理中创建。</p>
                                            </div>
                                        ) : (
                                            <>
                                                <select
                                                    className="w-full h-10 px-3 text-sm rounded-lg border border-gray-200 dark:border-[#2a2d35] bg-white dark:bg-[#1c1f26] text-zinc-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                                                    value={multisigForm.gas_wallet_id ?? ''}
                                                    onChange={(e) => setMultisigForm(f => ({ ...f, gas_wallet_id: e.target.value ? Number(e.target.value) : null }))}
                                                >
                                                    <option value="">自动选择（余额最高的）</option>
                                                    {bscGasWallets.map(gw => (
                                                        <option key={gw.id} value={gw.id}>
                                                            {gw.label || gw.address?.slice(0, 10) + '...'} — {gw.native_balance ? `${Number(gw.native_balance).toFixed(4)} BNB` : '余额未知'}
                                                        </option>
                                                    ))}
                                                </select>
                                                {!multisigForm.gas_wallet_id && (
                                                    <p className="text-[11px] text-gray-400">未选择时，系统将自动使用 BNB 余额最高的 Gas 钱包。</p>
                                                )}
                                            </>
                                        )}
                                    </div>
                                )
                            })()}

                            <div className="flex gap-2">
                                <Button variant="outline" className="flex-1" onClick={() => setMultisigStep(1)}>上一步</Button>
                                <Button variant="primary" className="flex-1 gap-1" disabled={isDeploying || (multisigForm.chain === 'BSC' && wallets.filter(w => w.type === 'gas' && w.chain === 'BSC' && w.address).length === 0)}
                                    onClick={async () => {
                                        setIsDeploying(true)
                                        try {
                                            const apiOwners = multisigForm.owners.map(o =>
                                                o.admin_id ? { admin_id: o.admin_id } : { address: o.address }
                                            )
                                            await settingsApi.createMultisigWallet({
                                                chain: multisigForm.chain,
                                                type: multisigForm.type,
                                                label: multisigForm.label || undefined,
                                                owners: apiOwners,
                                                threshold: multisigForm.threshold,
                                                gas_wallet_id: multisigForm.gas_wallet_id ?? undefined,
                                            })
                                            if (multisigForm.chain === 'TRON') {
                                                toast.success('合约部署已提交，正在链上确认，请稍候...')
                                            } else {
                                                toast.success('Safe 部署已提交，正在链上确认，请稍候...')
                                            }
                                            setShowMultisigCreate(false)
                                            fetchWallets()
                                        } catch (err: any) {
                                            toast.error(err?.response?.data?.detail || '创建失败')
                                        } finally {
                                            setIsDeploying(false)
                                        }
                                    }}>
                                    {isDeploying && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {multisigForm.chain === 'TRON' ? '部署合约' : '部署 Safe'}
                                </Button>
                            </div>
                        </>
                    )}
                </div>
            </Modal>

            {/* 导入多签钱包 Modal */}
            <Modal isOpen={showMultisigImport} onClose={() => setShowMultisigImport(false)} title="导入多签钱包">
                <div className="flex flex-col gap-4">
                    <div>
                        <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">链</label>
                        <Select value={importForm.chain} onChange={(v) => setImportForm(f => ({ ...f, chain: v }))}
                            options={[{ value: 'BSC', label: 'BSC (Gnosis Safe)' }, { value: 'TRON', label: 'TRON (原生多签)' }]} />
                    </div>
                    <div>
                        <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">类型</label>
                        <Select value={importForm.type} onChange={(v) => setImportForm(f => ({ ...f, type: v }))}
                            options={[{ value: 'collection', label: '归集钱包' }, { value: 'payout', label: '打款钱包' }]} />
                    </div>
                    <div>
                        <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">多签地址</label>
                        <Input value={importForm.address} onChange={(e) => setImportForm(f => ({ ...f, address: e.target.value }))}
                            placeholder={importForm.chain === 'BSC' ? '0x...' : 'T...'} />
                    </div>
                    <div>
                        <label className="text-xs text-gray-500 dark:text-gray-400 mb-1 block">标签（可选）</label>
                        <Input value={importForm.label} onChange={(e) => setImportForm(f => ({ ...f, label: e.target.value }))}
                            placeholder="如: BSC 归集 Safe" />
                    </div>
                    <p className="text-xs text-gray-400">
                        导入时将从链上验证该地址的 owners 和 threshold 信息。
                    </p>
                    <Button variant="primary" className="w-full gap-1" disabled={isImporting || !importForm.address.trim()}
                        onClick={async () => {
                            setIsImporting(true)
                            try {
                                await settingsApi.importMultisigWallet({
                                    chain: importForm.chain,
                                    type: importForm.type,
                                    address: importForm.address.trim(),
                                    label: importForm.label || undefined,
                                })
                                toast.success('多签钱包导入成功')
                                setShowMultisigImport(false)
                                fetchWallets()
                            } catch (err: any) {
                                toast.error(err?.response?.data?.detail || '导入失败')
                            } finally {
                                setIsImporting(false)
                            }
                        }}>
                        {isImporting && <Loader2 className="w-4 h-4 animate-spin" />}
                        验证并导入
                    </Button>
                </div>
            </Modal>
        </div>
    )
}
