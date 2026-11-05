"""project_activity: pushes made through the API

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-05 07:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'project_activity',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(32), nullable=True),
        sa.Column('client', sa.String(120), nullable=False, server_default=''),
        sa.Column('ok', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('paths', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=False, server_default=''),
        sa.Column('rev', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_project_activity_project_id', 'project_activity', ['project_id'])
    op.create_unique_constraint('uq_project_activity_rev', 'project_activity', ['project_id', 'rev'])


def downgrade() -> None:
    op.drop_index('ix_project_activity_project_id', table_name='project_activity')
    op.drop_table('project_activity')
