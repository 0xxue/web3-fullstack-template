export type Chain = 'bsc' | 'tron'
export type AdminRole = 'super_admin' | 'operator' | 'signer' | 'viewer'
export type FeatureModule =
  | 'dashboard' | 'deposits' | 'collections' | 'addresses'
  | 'payouts' | 'multisig' | 'admin_manage' | 'system_params'
  | 'wallet_config' | 'telegram_config' | 'audit_logs'
export type DepositStatus = 'pending' | 'confirming' | 'confirmed'
export type PayoutStatus = 'pending' | 'signing' | 'executing' | 'completed' | 'failed'
export type ProposalType = 'collection' | 'transfer' | 'payout' | 'payout_batch'
export type ProposalStatus = 'pending' | 'signing' | 'executing' | 'executed' | 'rejected' | 'expired' | 'failed'
export type BatchStatus = 'pending' | 'approved' | 'executing' | 'completed' | 'partial' | 'failed'

export interface AdminUser {
  id: number
  username: string
  role: AdminRole
  signer_address_bsc: string | null
  signer_address_tron: string | null
  tg_chat_id: string | null
  is_active: boolean
  created_at: string
}

export interface DashboardData {
  wallets: WalletBalance[]
  today_deposits: { count: number; amount: string }
  today_payouts: { count: number; amount: string }
  pending_proposals: number
}

export interface WalletBalance {
  type: 'collect' | 'payout'
  chain: Chain
  address: string
  usdt_balance: string
  gas_balance: string
}

export interface DepositAddress {
  id: number
  chain: Chain
  address: string
  balance_usdt: string
  label: string
  last_collected_at: string | null
  created_at: string
}

export interface Deposit {
  id: number
  chain: string
  token: string
  address: string
  from_address: string | null
  amount: string
  tx_hash: string
  block_number: number
  confirmations: number
  status: DepositStatus
  created_at: string
  confirmed_at: string | null
}

export interface DepositStats {
  total_today: number
  amount_today: string
  amount_by_token: { token: string; amount: string }[]
  pending_count: number
  confirming_count: number
  confirmed_today: number
}

export interface Payout {
  id: number
  chain: Chain
  to_address: string
  amount: string
  memo: string
  proposal_id: number
  tx_hash: string | null
  status: PayoutStatus
  created_by: string
  created_at: string
}

export interface ProposalSignature {
  id: number
  signer_id: number
  signer_address: string
  signer_username: string | null
  signed_at: string
}

export interface MultisigProposal {
  id: number
  chain: string
  type: ProposalType
  status: ProposalStatus
  title: string
  description: string | null
  wallet_id: number | null
  wallet_address: string | null
  to_address: string | null
  amount: string | null
  token: string | null   // 'usdt' | 'native'
  safe_tx_hash: string | null
  tx_data: { _memo?: string; _token?: string; _gas_tx_hash?: string; _gas_amount?: string; _gas_from?: string; [key: string]: unknown } | null
  threshold: number
  current_signatures: number
  owners: string[] | null
  signatures: ProposalSignature[]
  created_by: number | null
  created_by_username: string | null
  execution_tx_hash: string | null
  executed_at: string | null
  expires_at: string | null
  created_at: string
  updated_at: string
}

export interface CollectionBatch {
  id: number
  chain: Chain
  total_addresses: number
  total_amount: string
  success_count: number
  fail_count: number
  status: BatchStatus
  proposal_id: number
  scheduled_at: string | null
  executed_at: string | null
}

export interface CollectionScanResult {
  addresses: { address: string; balance: string; label: string }[]
  total_amount: string
  estimated_gas: string
  count: number
}

export interface AuditLogEntry {
  id: number
  admin_username: string
  action: string
  detail: string
  ip_address: string
  created_at: string
}

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface PaginatedResponse<T> {
  code: number
  message: string
  data: {
    items: T[]
    total: number
    page: number
    page_size: number
  }
}
