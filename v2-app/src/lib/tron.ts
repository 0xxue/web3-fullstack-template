/**
 * TronLink TRON 集成
 *
 * TronLink 注入 window.tronWeb + window.tronLink
 */

export type TronWalletId = 'tronlink' | 'okx' | 'tp' | 'auto'

export interface TronWebInstance {
    ready: boolean
    defaultAddress: {
        base58: string
        hex: string
    }
    trx: {
        sign: (transaction: Record<string, unknown>) => Promise<Record<string, unknown>>
        signMessageV2: (message: string) => Promise<string>
    }
}

declare global {
    interface Window {
        tronWeb?: TronWebInstance
        tronLink?: {
            request: (args: { method: string }) => Promise<unknown>
            isTronLink?: boolean
        }
        okxwallet?: {
            tronLink?: {
                request: (args: { method: string }) => Promise<unknown>
            }
            tronWeb?: TronWebInstance
        }
        tp?: {
            getTronAddress?: () => Promise<{ address: string }>
        }
        tokenpocket?: {
            isTronLink?: boolean
            tron?: unknown
        }
    }
}

// ─── 当前选择的 TRON 钱包 ───────────────────────────
let _selectedWallet: TronWalletId = 'auto'

export function setSelectedTronWallet(wallet: TronWalletId) {
    _selectedWallet = wallet
}

export function getSelectedTronWallet(): TronWalletId {
    return _selectedWallet
}

/** 检测所有可用的 TRON 钱包 */
export function detectTronWallets(): { id: TronWalletId; name: string; available: boolean; conflicted?: boolean; address?: string }[] {
    const wallets: { id: TronWalletId; name: string; available: boolean; conflicted?: boolean; address?: string }[] = []
    const addr = window.tronWeb?.defaultAddress?.base58
    const tronWebEx = window.tronWeb as unknown as Record<string, unknown> | undefined

    // OKX 钱包：window.okxwallet.tronLink 或 isOKExWallet 标记
    const hasOKX = !!(window.okxwallet?.tronLink || tronWebEx?.isOKExWallet)
    wallets.push({ id: 'okx', name: 'OKX Wallet', available: hasOKX, address: hasOKX ? addr : undefined })

    // TokenPocket：window.tp（移动端）、window.tokenpocket（桌面扩展）或 isTokenPocket 标记
    const hasTP = !!(window.tp || window.tokenpocket || tronWebEx?.isTokenPocket)
    wallets.push({ id: 'tp', name: 'TokenPocket', available: hasTP, address: hasTP ? addr : undefined })

    // TronLink：window.tronLink 存在即视为已安装（各钱包共存时不互相排斥）
    const hasTronLink = !!window.tronLink
    wallets.push({ id: 'tronlink', name: 'TronLink', available: hasTronLink, address: hasTronLink ? addr : undefined })

    return wallets
}

/** 根据选择获取对应的 tronWeb 实例 */
function getTronWeb(): TronWebInstance | null {
    if (window.tronWeb?.defaultAddress?.base58) return window.tronWeb
    return null
}

export function isTronWebAvailable(): boolean {
    return getTronWeb() !== null
}

export async function requestTronAccess(): Promise<string | null> {

    if (window.tronLink) {
        try {
            const res = await window.tronLink.request({ method: 'tron_requestAccounts' }) as { code?: number }
            for (let i = 0; i < 10; i++) {
                await new Promise(resolve => setTimeout(resolve, 300))
                const addr = window.tronWeb?.defaultAddress?.base58
                if (addr && addr !== '') {
                    return addr
                }
            }
        } catch (e) {
            console.warn('[TRON] TronLink request failed:', e)
        }
    }

    const tw = getTronWeb()
    if (tw?.defaultAddress?.base58) {
        return tw.defaultAddress.base58
    }

    return null
}

export function getTronAddress(): string | null {
    const tw = getTronWeb()
    if (!tw) return null
    return tw.defaultAddress?.base58 || null
}

export async function signTronMessage(
    message: string
): Promise<string> {
    const tw = getTronWeb()
    if (!tw) {
        throw new Error('TRON 钱包未连接')
    }
    const signature = await tw.trx.signMessageV2(message)
    return signature
}

/** 使用外部 provider（WalletConnect）签名消息 */
export async function signTronMessageWithProvider(
    provider: TronWebInstance,
    message: string,
): Promise<string> {
    return await provider.trx.signMessageV2(message)
}

export async function signTronTransaction(
    rawTransaction: Record<string, unknown>
): Promise<{ signedTx: Record<string, unknown>; signature: string }> {
    const tw = getTronWeb()
    if (!tw) {
        throw new Error('TRON 钱包未连接')
    }

    const signFn = tw.trx.sign as (
        tx: Record<string, unknown>,
        pk?: undefined,
        useTronHeader?: boolean,
        multisig?: boolean
    ) => Promise<Record<string, unknown>>

    let signed: Record<string, unknown> | undefined

    // 方案1: trx.multiSign() — 优先用 Owner Permission（id=0）
    // TronLink multiSign 专为多签设计，签名 SHA256(raw_data_hex)
    const trxObj = tw.trx as unknown as Record<string, unknown>
    if (typeof trxObj.multiSign === 'function') {
        const multiSignFn = trxObj.multiSign as (
            tx: Record<string, unknown>, pk: undefined, permId: number
        ) => Promise<Record<string, unknown>>

        for (const permId of [0, 2]) {
            if (signed) break
            try {
                const result = await multiSignFn(rawTransaction, undefined, permId)
                const sigs = (result as { signature?: string[] }).signature
                if (sigs && sigs.length > 0) {
                    signed = result
                } else {
                    console.warn('[TRON] multiSign permId:', permId, 'returned no signature')
                }
            } catch (e) {
                console.warn('[TRON] multiSign permId:', permId, 'failed:', e)
            }
        }
    }

    // 方案2: 原始交易 + trx.sign()（不修改 JSON，避免 TronLink mobile 重新编码 raw_data）
    // 注意：不 patch owner_address，TronLink 使用 raw_data_hex 计算签名哈希
    if (!signed) {
        try {
            const result = await signFn(rawTransaction, undefined, false, false)
            if (result && (result as { signature?: unknown }).signature) {
                signed = result
            } else {
                console.warn('[TRON] sign() returned no signature')
            }
        } catch (e) {
            console.warn('[TRON] sign() threw:', e)
        }
    }

    // 方案3: 原始交易 + multisig=true（TronLink 旧版回退）
    if (!signed) {
        signed = await signFn(rawTransaction, undefined, false, true)
    }

    const signatures = (signed as { signature?: string[] } | undefined)?.signature
    if (!signatures || signatures.length === 0) {
        throw new Error('签名失败：未获取到签名数据')
    }
    return {
        signedTx: signed,
        signature: signatures[0],
    }
}

/** 使用外部 provider（WalletConnect）签名多签交易 */
export async function signTronTransactionWithProvider(
    provider: TronWebInstance,
    rawTransaction: Record<string, unknown>,
): Promise<{ signedTx: Record<string, unknown>; signature: string }> {
    const signFn = provider.trx.sign as (
        tx: Record<string, unknown>,
        pk?: undefined,
        useTronHeader?: boolean,
        multisig?: boolean
    ) => Promise<Record<string, unknown>>

    const signed = await signFn(rawTransaction, undefined, false, false)
    const signatures = (signed as { signature?: string[] } | undefined)?.signature
    if (!signatures || signatures.length === 0) {
        throw new Error('WalletConnect 签名失败：未获取到签名数据')
    }
    return { signedTx: signed, signature: signatures[0] }
}
