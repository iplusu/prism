"""builds.pages: page count of the compiled PDF

Revision ID: a1b2c3d4e5f6
Revises: 65d26d03dad2
Create Date: 2026-11-01 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '65d26d03dad2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('builds', sa.Column('pages', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('builds', 'pages')
