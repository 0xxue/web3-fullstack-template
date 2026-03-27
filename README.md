# Web3 Full-Stack Template

<p align="center">
  <img src="https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=black" />
  <img src="https://img.shields.io/badge/TypeScript-5.9-3178C6?style=for-the-badge&logo=typescript&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Solidity-0.8-363636?style=for-the-badge&logo=solidity&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" />
</p>

<p align="center">
  Production-ready Web3 full-stack template with multi-signature wallet, BSC + TRON dual-chain support, HD wallet derivation, RBAC permissions, and automated fund management workflows.
</p>

---

## Features

### Web3 Core

- **2/3 Multi-Signature** — 3 key holders, 2 signatures required for any transfer
- **Dual Chain** — BSC (Gnosis Safe) + TRON (custom TronMultiSig.sol)
- **HD Wallet** — BIP-32/BIP-44 derivation, unlimited deposit addresses from single seed
- **Deposit Scanner** — Background worker monitoring blockchain events, checkpoint-based resume
- **Auto Fund Flow** — Deposit detection → collection → multi-sig payout, fully automated
- **Mobile Signing** — v-value normalization for DApp browser compatibility
- **WalletConnect** — Cross-device wallet interaction

### Full-Stack

- **FastAPI Backend** — Async Python with auto OpenAPI docs
- **React 19 Frontend** — TypeScript + TanStack Router + Tailwind CSS + Wagmi
- **RBAC Permissions** — 4-tier roles (SuperAdmin / Operator / Signer / ReadOnly)
- **JWT + 2FA** — Access/refresh tokens + TOTP authentication
- **PostgreSQL + Redis** — Async connection pool + caching
- **Alembic Migrations** — Database schema versioning
- **Structured Logging** — JSON format
- **Login Rate Limiting** — Brute-force protection
- **Telegram Notifications** — Real-time alerts for proposals and payouts

### DevOps

- **Docker Compose** — Full stack (API + frontend + DB + Redis)
- **CI/CD** — GitHub Actions
- **Makefile** — `make dev`, `make test`, `make migrate`

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React 19)                    │
│        TanStack Router + Wagmi + TRON Adapter            │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                  Backend (FastAPI)                        │
│                                                          │
│  Auth (JWT+2FA) | RBAC | Proposals | Deposits | Wallets │
│                                                          │
│  Services: Chain Client (BSC+TRON) | Deposit Scanner     │
│            Collection Executor | Payout Executor          │
│            Multi-sig Service | HD Wallet Derivation       │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                  Blockchain Layer                         │
│  BSC: Web3.py + Gnosis Safe SDK + Alchemy RPC            │
│  TRON: TronWeb + TronMultiSig.sol + TronGrid             │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│               Data Layer                                 │
│  PostgreSQL (13 tables) | Redis | Alembic Migrations     │
└─────────────────────────────────────────────────────────┘
```

### Fund Flow

```
User Deposit → Auto Detection → Collection (2/3 multi-sig)
→ Hot Wallet → Payout (2/3 multi-sig) → Withdrawal
```

---

## Smart Contracts

| Contract | Chain | Description |
|----------|-------|-------------|
| `TronMultiSig.sol` | TRON | Custom k-of-n multi-sig with nonce replay protection |
| Gnosis Safe | BSC | Industry-standard multi-sig framework |

---

## Quick Start

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env  # Configure RPC URLs, API keys
alembic upgrade head
uvicorn app.main:app --reload

# Frontend
cd v2-app
npm install
npm run dev

# Docker
docker compose up -d
```

---

## Configuration

Key environment variables (see `.env.example`):

```env
# Database
DATABASE_URL=postgresql+asyncpg://...

# Blockchain RPC
BSC_RPC_URL=https://bsc-dataseed.binance.org
TRON_RPC_URL=https://api.trongrid.io

# HD Wallet
HD_MNEMONIC=your twelve word mnemonic phrase here

# Alchemy (BSC)
ALCHEMY_API_KEY=your_key

# Telegram Notifications
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## RBAC Roles

| Role | Permissions |
|------|------------|
| Super Admin | Full access |
| Operator | Manage wallets, trigger collections |
| Signer | View proposals, sign transactions |
| Read Only | View dashboards only |

---

## Project Structure

```
├── backend/
│   ├── app/
│   │   ├── api/              # REST endpoints (13 modules)
│   │   ├── core/             # Auth, RBAC, HD wallet, Telegram
│   │   ├── middleware/       # Logging, rate limit, audit
│   │   ├── models/          # SQLAlchemy (13 tables)
│   │   ├── services/        # Chain client, scanner, executors
│   │   └── schemas/         # Pydantic validation
│   ├── alembic/             # Database migrations
│   ├── tests/
│   └── Dockerfile
├── contracts/               # Solidity smart contracts
│   ├── TronMultiSig.sol
│   └── TronMultiSig.abi.json
├── v2-app/                  # React 19 frontend
├── docs/                    # Technical docs
├── docker-compose.yml
├── Makefile
└── .github/workflows/
```

---

## License

MIT

---

# 中文说明

## Web3 全栈模板

生产就绪的 Web3 全栈模板，包含多签钱包、BSC + TRON 双链、HD 钱包派生、RBAC 权限、自动化资金管理。

### 核心能力

- **2/3 多签** — BSC (Gnosis Safe) + TRON (自研合约)
- **HD 钱包** — 一个主密钥派生无限充值地址
- **全自动资金流转** — 充值监控 → 自动归集 → 多签打款
- **RBAC 4 级权限** — 超管 / 操作员 / 签名人 / 只读
- **JWT + 2FA** — 双因素认证
- **Telegram 通知** — 多签进度实时推送

### 快速开始

```bash
docker compose up -d
```

---

<p align="center">
  <sub>Built by <a href="https://github.com/0xxue">0xxuebao</a></sub>
</p>
