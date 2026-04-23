"""...

Revision ID: 30d77bbf26e2
Revises: f8210194a887
Create Date: 2026-02-13 11:18:08.473801

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '30d77bbf26e2'
down_revision: Union[str, Sequence[str], None] = 'f8210194a887'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
