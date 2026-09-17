"""Reviewed requirement ledger and recoverable CSV projection."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('extraction_tasks', sa.Column('registration_json', sa.Text(), nullable=False, server_default='{}'))
    op.create_table('requirement_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dedup_key', sa.String(64), nullable=False, unique=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('extraction_tasks.id'), nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('sources.id'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('account_key', sa.String(100), nullable=False),
        sa.Column('source_label', sa.String(300), nullable=False),
        sa.Column('conversation_title', sa.String(300), nullable=False),
        sa.Column('start_time', sa.Integer(), nullable=False),
        sa.Column('end_time', sa.Integer(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('uncertainties', sa.Text(), nullable=False),
        sa.Column('evidence_json', sa.Text(), nullable=False),
        sa.Column('registered_at', sa.String(100), nullable=False),
        sa.Column('status', sa.String(30), nullable=False))
    op.create_table('requirement_export',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('exported_at', sa.String(100), nullable=False))


def downgrade():
    op.drop_table('requirement_export')
    op.drop_table('requirement_records')
    op.drop_column('extraction_tasks', 'registration_json')
