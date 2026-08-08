"""add m6 membership plans and usage logs

Revision ID: c1a2b3d4e5f6
Revises: 8ea157a2625d
Create Date: 2026-08-08 18:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, None] = '8ea157a2625d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLANS = [
    {"code": "free", "name": "免费会员", "sort_order": 0,
     "price_monthly_cents": 0, "price_yearly_cents": 0,
     "quotas": {"stock_analysis": 1, "sector": 0, "dragon_tiger": 0, "holdings": 0,
                "ai_watch": 0, "monitor": 0, "risk_alert": 0, "stock_pick": 0}},
    {"code": "D", "name": "D 档会员", "sort_order": 1,
     "price_monthly_cents": 8800, "price_yearly_cents": 88000,
     "quotas": {"stock_analysis": 5, "sector": 0, "dragon_tiger": 0, "holdings": 0,
                "ai_watch": 0, "monitor": 0, "risk_alert": 0, "stock_pick": 0}},
    {"code": "C", "name": "C 档会员", "sort_order": 2,
     "price_monthly_cents": 12800, "price_yearly_cents": 128000,
     "quotas": {"stock_analysis": 8, "sector": -1, "dragon_tiger": -1, "holdings": 0,
                "ai_watch": 0, "monitor": 0, "risk_alert": 0, "stock_pick": 0}},
    {"code": "B", "name": "B 档会员", "sort_order": 3,
     "price_monthly_cents": 0, "price_yearly_cents": 4000000,
     "quotas": {"stock_analysis": 20, "sector": -1, "dragon_tiger": -1, "holdings": -1,
                "ai_watch": -1, "monitor": -1, "risk_alert": -1, "stock_pick": 0}},
    {"code": "A", "name": "A 档会员", "sort_order": 4,
     "price_monthly_cents": 0, "price_yearly_cents": 8888800,
     "quotas": {"stock_analysis": -1, "sector": -1, "dragon_tiger": -1, "holdings": -1,
                "ai_watch": -1, "monitor": -1, "risk_alert": -1, "stock_pick": -1}},
]


def upgrade() -> None:
    plans = op.create_table(
        'membership_plans',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=8), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('price_monthly_cents', sa.Integer(), nullable=False),
        sa.Column('price_yearly_cents', sa.Integer(), nullable=False),
        sa.Column('quotas', sa.JSON(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_membership_plans_code', 'membership_plans', ['code'], unique=True)

    op.create_table(
        'usage_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('feature', sa.String(length=32), nullable=False),
        sa.Column('used_on', sa.Date(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'feature', 'used_on', name='uq_usage_user_feature_day'),
    )
    op.create_index('ix_usage_logs_user_id', 'usage_logs', ['user_id'])
    op.create_index('ix_usage_logs_used_on', 'usage_logs', ['used_on'])

    op.bulk_insert(plans, [{**p, "is_active": True} for p in PLANS])


def downgrade() -> None:
    op.drop_index('ix_usage_logs_used_on', table_name='usage_logs')
    op.drop_index('ix_usage_logs_user_id', table_name='usage_logs')
    op.drop_table('usage_logs')
    op.drop_index('ix_membership_plans_code', table_name='membership_plans')
    op.drop_table('membership_plans')
