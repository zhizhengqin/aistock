"""Add ai_trade_plans and ai_decision_records tables (F-08-04).

Revision ID: d7e8f9a0b1c2
Revises: c1a2b3d4e5f6
Create Date: 2026-08-10
"""
revision = "d7e8f9a0b1c2"
down_revision = "c1a2b3d4e5f6"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "ai_trade_plans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("config_id", sa.Integer, sa.ForeignKey("monitor_configs.id"), nullable=True, index=True),
        sa.Column("stock_code", sa.String(16), nullable=False, index=True),
        sa.Column("stock_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("action", sa.String(16), nullable=False, server_default="hold"),
        sa.Column("suggested_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("target_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("stop_loss", sa.Float, nullable=False, server_default="0"),
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("plan_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_table(
        "ai_decision_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("config_id", sa.Integer, sa.ForeignKey("monitor_configs.id"), nullable=True, index=True),
        sa.Column("stock_code", sa.String(16), nullable=False, index=True),
        sa.Column("stock_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("decision_type", sa.String(32), nullable=False, server_default="monitor_check"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("detail_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("ai_decision_records")
    op.drop_table("ai_trade_plans")
