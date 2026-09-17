"""Chat archival and durable manual sync jobs."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('conversations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('sources.id'), nullable=False),
        sa.Column('conversation_type', sa.Integer(), nullable=False),
        sa.Column('target_id', sa.String(200), nullable=False),
        sa.Column('title', sa.String(300), nullable=False),
        sa.UniqueConstraint('source_id', 'conversation_type', 'target_id'))
    op.create_table('messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('dedup_key', sa.String(100), nullable=False),
        sa.Column('sender_id', sa.String(200), nullable=False),
        sa.Column('sender_name', sa.String(300), nullable=False),
        sa.Column('sent_time', sa.Integer(), nullable=False),
        sa.Column('sent_time_readable', sa.String(100), nullable=False),
        sa.Column('message_type', sa.String(100), nullable=False),
        sa.Column('text_content', sa.Text(), nullable=False),
        sa.Column('content_json', sa.Text(), nullable=False),
        sa.UniqueConstraint('conversation_id', 'dedup_key'))
    op.create_index('ix_messages_time', 'messages', ['conversation_id', 'sent_time'])
    op.create_table('sync_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('sources.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('start_time', sa.Integer(), nullable=False),
        sa.Column('end_time', sa.Integer(), nullable=False),
        sa.Column('settings_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.String(100), nullable=False),
        sa.Column('updated_at', sa.String(100), nullable=False),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False),
        sa.Column('error_code', sa.String(50), nullable=False))
    op.create_table('sync_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('sync_tasks.id'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('cursor', sa.Integer(), nullable=False),
        sa.Column('pages', sa.Integer(), nullable=False),
        sa.Column('fetched', sa.Integer(), nullable=False),
        sa.Column('inserted', sa.Integer(), nullable=False),
        sa.Column('oldest', sa.Integer(), nullable=True),
        sa.Column('newest', sa.Integer(), nullable=True),
        sa.Column('error_code', sa.String(50), nullable=False),
        sa.UniqueConstraint('task_id', 'conversation_id'))


def downgrade():
    for name in ('sync_items', 'sync_tasks', 'messages', 'conversations'):
        op.drop_table(name)
