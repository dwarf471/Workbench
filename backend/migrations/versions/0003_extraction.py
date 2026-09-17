"""Durable requirement extraction snapshots and review drafts."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('extraction_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('sources.id'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('start_time', sa.Integer(), nullable=False),
        sa.Column('end_time', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('config_json', sa.Text(), nullable=False),
        sa.Column('candidates_json', sa.Text(), nullable=False),
        sa.Column('total_messages', sa.Integer(), nullable=False),
        sa.Column('skipped_messages', sa.Integer(), nullable=False),
        sa.Column('total_chunks', sa.Integer(), nullable=False),
        sa.Column('completed_chunks', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(50), nullable=False),
        sa.Column('created_at', sa.String(100), nullable=False),
        sa.Column('updated_at', sa.String(100), nullable=False))


def downgrade():
    op.drop_table('extraction_tasks')
