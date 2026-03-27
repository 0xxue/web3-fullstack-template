"""Initial schema

Revision ID: 0001
Create Date: 2025-11-15

All tables for the multi-signature wallet management system.
"""

from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Admin users
    op.create_table('admins',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(100), unique=True, nullable=False),
        sa.Column('email', sa.String(200)),
        sa.Column('password_hash', sa.String(200), nullable=False),
        sa.Column('role', sa.String(20), default='operator'),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('totp_secret', sa.String(100)),
        sa.Column('token_version', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), onupdate=sa.func.now()),
    )

    # Wallets (Gnosis Safe + TRON multi-sig)
    op.create_table('wallets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('chain', sa.String(20), nullable=False),  # bsc / tron
        sa.Column('address', sa.String(200), unique=True, nullable=False),
        sa.Column('wallet_type', sa.String(20), default='hot'),  # hot / cold
        sa.Column('threshold', sa.Integer(), default=2),
        sa.Column('owners', sa.JSON()),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # User deposit addresses (HD derived)
    op.create_table('deposit_addresses',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('chain', sa.String(20), nullable=False),
        sa.Column('address', sa.String(200), unique=True, nullable=False),
        sa.Column('derivation_path', sa.String(100)),
        sa.Column('user_id', sa.String(100)),
        sa.Column('is_used', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # Deposits
    op.create_table('deposits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('chain', sa.String(20), nullable=False),
        sa.Column('tx_hash', sa.String(200), unique=True, nullable=False),
        sa.Column('from_address', sa.String(200)),
        sa.Column('to_address', sa.String(200)),
        sa.Column('amount', sa.Numeric(36, 18)),
        sa.Column('token', sa.String(20), default='USDT'),
        sa.Column('block_number', sa.BigInteger()),
        sa.Column('status', sa.String(20), default='confirmed'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # Multi-sig proposals
    op.create_table('proposals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('proposal_type', sa.String(20), nullable=False),  # collection / payout / transfer
        sa.Column('chain', sa.String(20), nullable=False),
        sa.Column('wallet_id', sa.Integer(), sa.ForeignKey('wallets.id')),
        sa.Column('to_address', sa.String(200)),
        sa.Column('amount', sa.Numeric(36, 18)),
        sa.Column('token', sa.String(20), default='USDT'),
        sa.Column('status', sa.String(20), default='pending'),  # pending / confirmed / executed / rejected
        sa.Column('threshold', sa.Integer(), default=2),
        sa.Column('signature_count', sa.Integer(), default=0),
        sa.Column('tx_hash', sa.String(200)),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('admins.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # Signatures
    op.create_table('signatures',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('proposals.id'), nullable=False),
        sa.Column('signer_address', sa.String(200), nullable=False),
        sa.Column('signature', sa.Text()),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admins.id')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # System settings
    op.create_table('system_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(100), unique=True, nullable=False),
        sa.Column('value', sa.JSON()),
        sa.Column('updated_at', sa.DateTime(), onupdate=sa.func.now()),
    )

    # Audit logs
    op.create_table('audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('admins.id')),
        sa.Column('action', sa.String(50)),
        sa.Column('resource', sa.String(50)),
        sa.Column('detail', sa.JSON()),
        sa.Column('ip_address', sa.String(50)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # Notifications
    op.create_table('notifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('title', sa.String(200)),
        sa.Column('content', sa.Text()),
        sa.Column('is_sent', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('notifications')
    op.drop_table('audit_logs')
    op.drop_table('system_settings')
    op.drop_table('signatures')
    op.drop_table('proposals')
    op.drop_table('deposits')
    op.drop_table('deposit_addresses')
    op.drop_table('wallets')
    op.drop_table('admins')
