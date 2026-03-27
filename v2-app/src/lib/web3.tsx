import { createAppKit } from '@reown/appkit/react'
import { WagmiProvider } from 'wagmi'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { WagmiAdapter } from '@reown/appkit-adapter-wagmi'
import { bsc } from '@reown/appkit/networks'

const queryClient = new QueryClient()

const projectId = 'f30627c695cc2c1a97d779f9306c9ead'

const metadata = {
    name: 'Vault Admin',
    description: 'Vault Admin Multisig Manager',
    url: 'https://vaultsign.cloud',
    icons: ['https://avatars.githubusercontent.com/u/179229932']
}

export const wagmiAdapter = new WagmiAdapter({
    networks: [bsc],
    projectId,
    ssr: false,
})

let appkit: ReturnType<typeof createAppKit> | null = null;
try {
    appkit = createAppKit({
        adapters: [wagmiAdapter],
        networks: [bsc],
        projectId,
        metadata,
        themeVariables: {
            '--w3m-accent': '#2563eb', // text-blue-600 to match the other blue button precisely
            '--w3m-border-radius-master': '1px', // 1px matches the 8px rounded corners of h-8 rounded-lg size="sm" precisely due to how the multiplier works
            '--w3m-font-family': '"Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif',
            '--w3m-z-index': 100,
        },
        features: { email: false, socials: false },
    })
} catch (e) {
    console.warn('[Web3] AppKit init failed (non-critical):', e)
}

export function Web3Provider({ children }: { children: React.ReactNode }) {
    return (
        <WagmiProvider config={wagmiAdapter.wagmiConfig}>
            <QueryClientProvider client={queryClient}>
                <AppKitThemeSync />
                {children}
            </QueryClientProvider>
        </WagmiProvider>
    )
}

// Helper to keep AppKit theme in sync with our global theme
import { useThemeStore } from '@/store/useThemeStore';
import { useEffect } from 'react';

function AppKitThemeSync() {
    const isDark = useThemeStore((s) => s.isDark)

    useEffect(() => {
        if (appkit) {
            appkit.setThemeMode(isDark ? 'dark' : 'light')
        }
    }, [isDark])

    return null
}
