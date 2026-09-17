"""Initial workbench source registry; no chat data in stage one."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sources', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('provider', sa.String(50), nullable=False),
                    sa.Column('account_key', sa.String(100), nullable=False),
                    sa.Column('label', sa.String(100), nullable=False),
                    sa.UniqueConstraint('provider', 'account_key'))


def downgrade():
    op.drop_table('sources')
