"""Merge point (client/company-x): the Company X production flow (0003) and the workforce module from main (0004).

Both revisions branch from 0002 and touch different tables, so the merge has no operations of its own.

Revision ID: 0005
Revises: 0003, 0004
Create Date: 2026-11-25 18:05:11.162053
"""
from typing import Sequence, Union


revision: str = '0005'
down_revision: Union[str, None] = ('0003', '0004')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
