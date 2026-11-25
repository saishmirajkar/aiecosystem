"""Workforce and shift management (foundation module).

Revision id 0004 is used on main so it never collides with client-branch revisions numbered 0003.

Revision ID: 0004
Revises: 0002
Create Date: 2026-11-24 18:15:02.122133
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # btree_gist lets the exclusion constraint compare employee UUIDs; it is a trusted extension (no superuser needed).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table('skills',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('kind', sa.Enum('skill', 'qualification', 'certification', name='skill_kind', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_skills')),
    sa.UniqueConstraint('code', name=op.f('uq_skills_code'))
    )
    op.create_table('work_locations',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_work_locations')),
    sa.UniqueConstraint('code', name=op.f('uq_work_locations_code'))
    )
    op.create_table('designations',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_designations_department_id_departments')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_designations')),
    sa.UniqueConstraint('code', name=op.f('uq_designations_code'))
    )
    op.create_table('holidays',
    sa.Column('holiday_date', sa.Date(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('work_location_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['work_location_id'], ['work_locations.id'], name=op.f('fk_holidays_work_location_id_work_locations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_holidays')),
    sa.UniqueConstraint('holiday_date', 'work_location_id', name='uq_holidays_date_location')
    )
    op.create_index(op.f('ix_holidays_holiday_date'), 'holidays', ['holiday_date'], unique=False)
    op.create_table('leave_types',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('is_paid', sa.Boolean(), nullable=False),
    sa.Column('annual_days', sa.Numeric(precision=6, scale=1), nullable=False),
    sa.Column('carry_forward_max', sa.Numeric(precision=6, scale=1), nullable=False),
    sa.Column('unlimited', sa.Boolean(), nullable=False),
    sa.Column('requires_document', sa.Boolean(), nullable=False),
    sa.Column('allow_half_day', sa.Boolean(), nullable=False),
    sa.Column('color', sa.String(length=20), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.CheckConstraint('annual_days >= 0 AND carry_forward_max >= 0', name=op.f('ck_leave_types_days_non_negative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_leave_types_created_by_users')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_leave_types_updated_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_types')),
    sa.UniqueConstraint('code', name=op.f('uq_leave_types_code'))
    )
    op.create_table('overtime_policies',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('daily_threshold_hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('weekly_threshold_hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('ot_multiplier', sa.Numeric(precision=6, scale=3), nullable=False),
    sa.Column('weekly_off_rule', sa.Enum('all_overtime', 'normal', name='weekly_off_rule', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('weekly_off_multiplier', sa.Numeric(precision=6, scale=3), nullable=False),
    sa.Column('holiday_rule', sa.Enum('all_overtime', 'normal', name='holiday_rule', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('holiday_multiplier', sa.Numeric(precision=6, scale=3), nullable=False),
    sa.Column('night_start', sa.Time(), nullable=True),
    sa.Column('night_end', sa.Time(), nullable=True),
    sa.Column('night_allowance_per_hour', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('rounding_minutes', sa.Integer(), nullable=False),
    sa.Column('rounding_mode', sa.Enum('down', 'nearest', 'up', name='rounding_mode', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('min_overtime_minutes', sa.Integer(), nullable=False),
    sa.Column('requires_approval', sa.Boolean(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.CheckConstraint('daily_threshold_hours > 0 AND weekly_threshold_hours > 0', name=op.f('ck_overtime_policies_thresholds_positive')),
    sa.CheckConstraint('ot_multiplier >= 0 AND weekly_off_multiplier >= 0 AND holiday_multiplier >= 0', name=op.f('ck_overtime_policies_multipliers_non_negative')),
    sa.CheckConstraint('rounding_minutes >= 0 AND min_overtime_minutes >= 0', name=op.f('ck_overtime_policies_rounding_non_negative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_overtime_policies_created_by_users')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_overtime_policies_updated_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_overtime_policies')),
    sa.UniqueConstraint('code', name=op.f('uq_overtime_policies_code'))
    )
    op.create_index('uq_overtime_policies_default', 'overtime_policies', ['is_default'], unique=True, postgresql_where=sa.text('is_default'))
    op.create_table('payroll_export_batches',
    sa.Column('number', sa.String(length=30), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=True),
    sa.Column('employee_count', sa.Integer(), nullable=False),
    sa.Column('line_count', sa.Integer(), nullable=False),
    sa.Column('total_minutes', sa.Integer(), nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_payroll_export_batches_created_by_users')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_payroll_export_batches_department_id_departments')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payroll_export_batches')),
    sa.UniqueConstraint('number', name=op.f('uq_payroll_export_batches_number'))
    )
    op.create_table('shift_patterns',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('cycle_days', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.CheckConstraint('cycle_days BETWEEN 1 AND 56', name=op.f('ck_shift_patterns_cycle_range')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_shift_patterns_created_by_users')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_shift_patterns_updated_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shift_patterns')),
    sa.UniqueConstraint('code', name=op.f('uq_shift_patterns_code'))
    )
    op.create_table('teams',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_teams_department_id_departments')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teams')),
    sa.UniqueConstraint('code', name=op.f('uq_teams_code'))
    )
    op.create_table('workforce_policies',
    sa.Column('department_id', sa.UUID(), nullable=True),
    sa.Column('auto_publish', sa.Boolean(), nullable=False),
    sa.Column('claim_deadline_hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('min_rest_hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('weekly_hour_cap', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('monthly_cover_hours_cap', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('withdrawal_cutoff_hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('auto_reopen_on_withdrawal', sa.Boolean(), nullable=False),
    sa.Column('allow_self_check_in', sa.Boolean(), nullable=False),
    sa.Column('half_day_auto_cover', sa.Boolean(), nullable=False),
    sa.Column('show_pay_estimates', sa.Boolean(), nullable=False),
    sa.Column('late_grace_minutes', sa.Integer(), nullable=False),
    sa.Column('half_day_min_hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('weekly_off_days', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.CheckConstraint('claim_deadline_hours >= 0 AND min_rest_hours >= 0 AND withdrawal_cutoff_hours >= 0', name=op.f('ck_workforce_policies_hours_non_negative')),
    sa.CheckConstraint('weekly_hour_cap IS NULL OR weekly_hour_cap > 0', name=op.f('ck_workforce_policies_weekly_cap_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_workforce_policies_created_by_users')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_workforce_policies_department_id_departments')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_workforce_policies_updated_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workforce_policies')),
    sa.UniqueConstraint('department_id', name='uq_workforce_policies_department')
    )
    op.create_index('uq_workforce_policies_company', 'workforce_policies', [sa.text('(department_id IS NULL)')], unique=True, postgresql_where=sa.text('department_id IS NULL'))
    op.create_table('employees',
    sa.Column('employee_code', sa.String(length=30), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=True),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('photo_attachment_id', sa.UUID(), nullable=True),
    sa.Column('department_id', sa.UUID(), nullable=False),
    sa.Column('designation_id', sa.UUID(), nullable=True),
    sa.Column('manager_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.Enum('active', 'on_leave', 'suspended', 'inactive', 'terminated', name='employee_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('employment_type', sa.Enum('permanent', 'contract', 'temporary', 'apprentice', 'part_time', name='employment_type', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('date_of_joining', sa.Date(), nullable=True),
    sa.Column('date_of_leaving', sa.Date(), nullable=True),
    sa.Column('work_location_id', sa.UUID(), nullable=True),
    sa.Column('shift_pattern_id', sa.UUID(), nullable=True),
    sa.Column('pattern_anchor_date', sa.Date(), nullable=True),
    sa.Column('overtime_eligible', sa.Boolean(), nullable=False),
    sa.Column('weekly_hour_limit', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('base_hourly_rate', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('overtime_policy_id', sa.UUID(), nullable=True),
    sa.Column('emergency_contact_name', sa.String(length=150), nullable=True),
    sa.Column('emergency_contact_phone', sa.String(length=50), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.CheckConstraint('base_hourly_rate IS NULL OR base_hourly_rate >= 0', name=op.f('ck_employees_rate_non_negative')),
    sa.CheckConstraint('manager_id IS NULL OR manager_id <> id', name=op.f('ck_employees_not_own_manager')),
    sa.CheckConstraint('weekly_hour_limit IS NULL OR weekly_hour_limit > 0', name=op.f('ck_employees_weekly_limit_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_employees_created_by_users')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_employees_department_id_departments')),
    sa.ForeignKeyConstraint(['designation_id'], ['designations.id'], name=op.f('fk_employees_designation_id_designations')),
    sa.ForeignKeyConstraint(['manager_id'], ['employees.id'], name=op.f('fk_employees_manager_id_employees')),
    sa.ForeignKeyConstraint(['photo_attachment_id'], ['attachments.id'], name=op.f('fk_employees_photo_attachment_id_attachments')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_employees_updated_by_users')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_employees_user_id_users')),
    sa.ForeignKeyConstraint(['work_location_id'], ['work_locations.id'], name=op.f('fk_employees_work_location_id_work_locations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employees')),
    sa.UniqueConstraint('user_id', name=op.f('uq_employees_user_id'))
    )
    op.create_index(op.f('ix_employees_department_id'), 'employees', ['department_id'], unique=False)
    op.create_index(op.f('ix_employees_manager_id'), 'employees', ['manager_id'], unique=False)
    op.create_index('uq_employees_code_lower', 'employees', [sa.text('lower(employee_code)')], unique=True)
    op.create_table('notification_deliveries',
    sa.Column('notification_id', sa.UUID(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=True),
    sa.Column('status', sa.Enum('delivered', 'pending', 'sent', 'logged', 'failed', 'skipped', name='delivery_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], name=op.f('fk_notification_deliveries_notification_id_notifications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_deliveries'))
    )
    op.create_index('ix_deliveries_pending', 'notification_deliveries', ['status', 'next_attempt_at'], unique=False)
    op.create_index(op.f('ix_notification_deliveries_notification_id'), 'notification_deliveries', ['notification_id'], unique=False)
    op.create_table('employee_skills',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('skill_id', sa.UUID(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('valid_until', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_employee_skills_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_employee_skills_skill_id_skills')),
    sa.PrimaryKeyConstraint('employee_id', 'skill_id', name=op.f('pk_employee_skills'))
    )
    op.create_table('leave_balances',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('leave_type_id', sa.UUID(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('entitled', sa.Numeric(precision=6, scale=1), nullable=False),
    sa.Column('carried_forward', sa.Numeric(precision=6, scale=1), nullable=False),
    sa.Column('adjusted', sa.Numeric(precision=6, scale=1), nullable=False),
    sa.Column('used', sa.Numeric(precision=6, scale=1), nullable=False),
    sa.CheckConstraint('used >= 0', name=op.f('ck_leave_balances_used_non_negative')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_leave_balances_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['leave_type_id'], ['leave_types.id'], name=op.f('fk_leave_balances_leave_type_id_leave_types')),
    sa.PrimaryKeyConstraint('employee_id', 'leave_type_id', 'year', name=op.f('pk_leave_balances'))
    )
    op.create_table('leave_requests',
    sa.Column('number', sa.String(length=30), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('leave_type_id', sa.UUID(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('half_day', sa.Enum('none', 'first_half', 'second_half', name='half_day', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('days', sa.Numeric(precision=6, scale=1), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('draft', 'pending', 'approved', 'rejected', 'cancelled', name='leave_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("half_day = 'none' OR start_date = end_date", name=op.f('ck_leave_requests_half_day_single_date')),
    sa.CheckConstraint('days >= 0', name=op.f('ck_leave_requests_days_non_negative')),
    sa.CheckConstraint('end_date >= start_date', name=op.f('ck_leave_requests_dates_ordered')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_leave_requests_created_by_users')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_leave_requests_decided_by_users')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_leave_requests_employee_id_employees')),
    sa.ForeignKeyConstraint(['leave_type_id'], ['leave_types.id'], name=op.f('fk_leave_requests_leave_type_id_leave_types')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_requests')),
    sa.UniqueConstraint('number', name=op.f('uq_leave_requests_number'))
    )
    op.create_index('ix_leave_requests_employee_dates', 'leave_requests', ['employee_id', 'start_date', 'end_date'], unique=False)
    op.create_index(op.f('ix_leave_requests_status'), 'leave_requests', ['status'], unique=False)
    op.create_table('overtime_calculations',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('work_date', sa.Date(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('is_correction', sa.Boolean(), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=False),
    sa.Column('day_type', sa.String(length=20), nullable=False),
    sa.Column('scheduled_minutes', sa.Integer(), nullable=False),
    sa.Column('worked_minutes', sa.Integer(), nullable=False),
    sa.Column('regular_minutes', sa.Integer(), nullable=False),
    sa.Column('ot_daily_minutes', sa.Integer(), nullable=False),
    sa.Column('ot_weekly_minutes', sa.Integer(), nullable=False),
    sa.Column('ot_weekly_off_minutes', sa.Integer(), nullable=False),
    sa.Column('ot_holiday_minutes', sa.Integer(), nullable=False),
    sa.Column('night_minutes', sa.Integer(), nullable=False),
    sa.Column('payable_minutes', sa.Integer(), nullable=False),
    sa.Column('approved_minutes', sa.Integer(), nullable=True),
    sa.Column('base_rate', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('multiplier', sa.Numeric(precision=6, scale=3), nullable=False),
    sa.Column('night_allowance', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('policy_id', sa.UUID(), nullable=True),
    sa.Column('policy_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Enum('calculated', 'approved', 'rejected', 'exported', name='calc_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('export_batch_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_overtime_calculations_decided_by_users')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_overtime_calculations_department_id_departments')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_overtime_calculations_employee_id_employees')),
    sa.ForeignKeyConstraint(['export_batch_id'], ['payroll_export_batches.id'], name=op.f('fk_overtime_calculations_export_batch_id_payroll_export_batches')),
    sa.ForeignKeyConstraint(['policy_id'], ['overtime_policies.id'], name=op.f('fk_overtime_calculations_policy_id_overtime_policies')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_overtime_calculations')),
    sa.UniqueConstraint('employee_id', 'work_date', 'revision', name='uq_overtime_calc_employee_date_rev')
    )
    op.create_index('ix_overtime_calc_status_date', 'overtime_calculations', ['status', 'work_date'], unique=False)
    op.create_table('shift_templates',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('break_minutes', sa.Integer(), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=False),
    sa.Column('work_location_id', sa.UUID(), nullable=True),
    sa.Column('team_id', sa.UUID(), nullable=True),
    sa.Column('required_count', sa.Integer(), nullable=False),
    sa.Column('supervisor_id', sa.UUID(), nullable=True),
    sa.Column('overtime_policy_id', sa.UUID(), nullable=True),
    sa.Column('color', sa.String(length=20), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.CheckConstraint('break_minutes >= 0 AND required_count >= 1', name=op.f('ck_shift_templates_counts_valid')),
    sa.CheckConstraint('start_time <> end_time', name=op.f('ck_shift_templates_non_zero_length')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_shift_templates_created_by_users')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_shift_templates_department_id_departments')),
    sa.ForeignKeyConstraint(['overtime_policy_id'], ['overtime_policies.id'], name=op.f('fk_shift_templates_overtime_policy_id_overtime_policies')),
    sa.ForeignKeyConstraint(['supervisor_id'], ['employees.id'], name=op.f('fk_shift_templates_supervisor_id_employees')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_shift_templates_team_id_teams')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_shift_templates_updated_by_users')),
    sa.ForeignKeyConstraint(['work_location_id'], ['work_locations.id'], name=op.f('fk_shift_templates_work_location_id_work_locations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shift_templates')),
    sa.UniqueConstraint('code', name=op.f('uq_shift_templates_code'))
    )
    op.create_table('team_members',
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_team_members_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_team_members_team_id_teams'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('team_id', 'employee_id', name=op.f('pk_team_members'))
    )
    op.create_table('leave_approvals',
    sa.Column('leave_request_id', sa.UUID(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_leave_approvals_actor_user_id_users')),
    sa.ForeignKeyConstraint(['leave_request_id'], ['leave_requests.id'], name=op.f('fk_leave_approvals_leave_request_id_leave_requests'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leave_approvals'))
    )
    op.create_index(op.f('ix_leave_approvals_leave_request_id'), 'leave_approvals', ['leave_request_id'], unique=False)
    op.create_table('shift_pattern_days',
    sa.Column('pattern_id', sa.UUID(), nullable=False),
    sa.Column('day_index', sa.Integer(), nullable=False),
    sa.Column('template_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['pattern_id'], ['shift_patterns.id'], name=op.f('fk_shift_pattern_days_pattern_id_shift_patterns'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['template_id'], ['shift_templates.id'], name=op.f('fk_shift_pattern_days_template_id_shift_templates')),
    sa.PrimaryKeyConstraint('pattern_id', 'day_index', name=op.f('pk_shift_pattern_days'))
    )
    op.create_table('shift_template_skills',
    sa.Column('template_id', sa.UUID(), nullable=False),
    sa.Column('skill_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_shift_template_skills_skill_id_skills')),
    sa.ForeignKeyConstraint(['template_id'], ['shift_templates.id'], name=op.f('fk_shift_template_skills_template_id_shift_templates'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('template_id', 'skill_id', name=op.f('pk_shift_template_skills'))
    )
    op.create_table('shifts',
    sa.Column('template_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('shift_date', sa.Date(), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('break_minutes', sa.Integer(), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=False),
    sa.Column('work_location_id', sa.UUID(), nullable=True),
    sa.Column('team_id', sa.UUID(), nullable=True),
    sa.Column('required_count', sa.Integer(), nullable=False),
    sa.Column('supervisor_id', sa.UUID(), nullable=True),
    sa.Column('overtime_policy_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.Enum('scheduled', 'cancelled', name='shift_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.CheckConstraint('break_minutes >= 0 AND required_count >= 0', name=op.f('ck_shifts_counts_valid')),
    sa.CheckConstraint('end_at > start_at', name=op.f('ck_shifts_window_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_shifts_created_by_users')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_shifts_department_id_departments')),
    sa.ForeignKeyConstraint(['overtime_policy_id'], ['overtime_policies.id'], name=op.f('fk_shifts_overtime_policy_id_overtime_policies')),
    sa.ForeignKeyConstraint(['supervisor_id'], ['employees.id'], name=op.f('fk_shifts_supervisor_id_employees')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_shifts_team_id_teams')),
    sa.ForeignKeyConstraint(['template_id'], ['shift_templates.id'], name=op.f('fk_shifts_template_id_shift_templates')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_shifts_updated_by_users')),
    sa.ForeignKeyConstraint(['work_location_id'], ['work_locations.id'], name=op.f('fk_shifts_work_location_id_work_locations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shifts'))
    )
    op.create_index(op.f('ix_shifts_department_id'), 'shifts', ['department_id'], unique=False)
    op.create_index(op.f('ix_shifts_shift_date'), 'shifts', ['shift_date'], unique=False)
    op.create_index('ix_shifts_window', 'shifts', ['start_at', 'end_at'], unique=False)
    op.create_index('uq_shifts_template_date', 'shifts', ['template_id', 'shift_date'], unique=True, postgresql_where=sa.text("template_id IS NOT NULL AND status = 'scheduled'"))
    op.create_table('overtime_opportunities',
    sa.Column('number', sa.String(length=30), nullable=False),
    sa.Column('shift_id', sa.UUID(), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=False),
    sa.Column('team_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('break_minutes', sa.Integer(), nullable=False),
    sa.Column('positions_total', sa.Integer(), nullable=False),
    sa.Column('positions_filled', sa.Integer(), nullable=False),
    sa.Column('claims_open_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claim_deadline', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('draft', 'published', 'open', 'partially_filled', 'filled', 'expired', 'cancelled', name='opportunity_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('source', sa.Enum('leave', 'manual', 'ai', 'withdrawal', name='opportunity_source', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('filled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('end_at > start_at', name=op.f('ck_overtime_opportunities_window_valid')),
    sa.CheckConstraint('positions_filled >= 0 AND positions_filled <= positions_total', name=op.f('ck_overtime_opportunities_filled_within_total')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_overtime_opportunities_created_by_users')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_overtime_opportunities_department_id_departments')),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], name=op.f('fk_overtime_opportunities_shift_id_shifts')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_overtime_opportunities_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_overtime_opportunities')),
    sa.UniqueConstraint('number', name=op.f('uq_overtime_opportunities_number'))
    )
    op.create_index('ix_opportunities_status_deadline', 'overtime_opportunities', ['status', 'claim_deadline'], unique=False)
    op.create_index(op.f('ix_overtime_opportunities_department_id'), 'overtime_opportunities', ['department_id'], unique=False)
    op.create_index(op.f('ix_overtime_opportunities_shift_id'), 'overtime_opportunities', ['shift_id'], unique=False)
    op.create_table('shift_positions',
    sa.Column('shift_id', sa.UUID(), nullable=False),
    sa.Column('slot', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('regular', 'cover', name='position_kind', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('window_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint("kind = 'regular' OR (window_start IS NOT NULL AND window_end > window_start)", name=op.f('ck_shift_positions_cover_window')),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], name=op.f('fk_shift_positions_shift_id_shifts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shift_positions')),
    sa.UniqueConstraint('shift_id', 'slot', name='uq_shift_positions_slot')
    )
    op.create_index(op.f('ix_shift_positions_shift_id'), 'shift_positions', ['shift_id'], unique=False)
    op.create_table('shift_skills',
    sa.Column('shift_id', sa.UUID(), nullable=False),
    sa.Column('skill_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], name=op.f('fk_shift_skills_shift_id_shifts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_shift_skills_skill_id_skills')),
    sa.PrimaryKeyConstraint('shift_id', 'skill_id', name=op.f('pk_shift_skills'))
    )
    op.create_table('opportunity_recipients',
    sa.Column('opportunity_id', sa.UUID(), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('notification_id', sa.UUID(), nullable=True),
    sa.Column('reachable', sa.Boolean(), nullable=False),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_opportunity_recipients_employee_id_employees')),
    sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], name=op.f('fk_opportunity_recipients_notification_id_notifications'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['opportunity_id'], ['overtime_opportunities.id'], name=op.f('fk_opportunity_recipients_opportunity_id_overtime_opportunities'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('opportunity_id', 'employee_id', 'round', name=op.f('pk_opportunity_recipients'))
    )
    op.create_table('shift_assignments',
    sa.Column('position_id', sa.UUID(), nullable=False),
    sa.Column('shift_id', sa.UUID(), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source', sa.Enum('schedule', 'marketplace', 'manual', name='assignment_source', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('status', sa.Enum('assigned', 'on_leave', 'released', 'cancelled', name='assignment_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('opportunity_id', sa.UUID(), nullable=True),
    sa.Column('leave_request_id', sa.UUID(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('rule_overrides', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('assigned_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end_reason', sa.Text(), nullable=True),
    sa.Column('ended_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    postgresql.ExcludeConstraint((sa.column('employee_id'), '='), (sa.text("tstzrange(start_at, end_at, '[)')"), '&&'), where=sa.text("status = 'assigned'"), using='gist', name='ex_assignments_no_overlap'),
    sa.CheckConstraint('end_at > start_at', name=op.f('ck_shift_assignments_window_valid')),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_shift_assignments_assigned_by_users')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_shift_assignments_employee_id_employees')),
    sa.ForeignKeyConstraint(['ended_by'], ['users.id'], name=op.f('fk_shift_assignments_ended_by_users')),
    sa.ForeignKeyConstraint(['position_id'], ['shift_positions.id'], name=op.f('fk_shift_assignments_position_id_shift_positions')),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], name=op.f('fk_shift_assignments_shift_id_shifts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shift_assignments'))
    )
    op.create_index('ix_assignments_employee_window', 'shift_assignments', ['employee_id', 'start_at'], unique=False)
    op.create_index(op.f('ix_shift_assignments_shift_id'), 'shift_assignments', ['shift_id'], unique=False)
    op.create_index('uq_assignments_active_position', 'shift_assignments', ['position_id'], unique=True, postgresql_where=sa.text("status = 'assigned'"))
    op.create_table('attendance_records',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('assignment_id', sa.UUID(), nullable=True),
    sa.Column('work_date', sa.Date(), nullable=False),
    sa.Column('scheduled_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('scheduled_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('check_in_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('check_out_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('break_minutes', sa.Integer(), nullable=False),
    sa.Column('worked_minutes', sa.Integer(), nullable=False),
    sa.Column('late_minutes', sa.Integer(), nullable=False),
    sa.Column('early_leave_minutes', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('present', 'absent', 'late', 'half_day', 'on_leave', 'holiday', 'weekly_off', name='attendance_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('source', sa.Enum('supervisor', 'self', 'import', 'correction', 'system', name='attendance_source', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('recorded_by', sa.UUID(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('check_out_at IS NULL OR check_in_at IS NULL OR check_out_at > check_in_at', name=op.f('ck_attendance_records_times_ordered')),
    sa.CheckConstraint('worked_minutes >= 0 AND break_minutes >= 0', name=op.f('ck_attendance_records_minutes_non_negative')),
    sa.ForeignKeyConstraint(['assignment_id'], ['shift_assignments.id'], name=op.f('fk_attendance_records_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_attendance_records_employee_id_employees')),
    sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], name=op.f('fk_attendance_records_recorded_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_records'))
    )
    op.create_index('ix_attendance_employee_date', 'attendance_records', ['employee_id', 'work_date'], unique=False)
    op.create_index('uq_attendance_assignment', 'attendance_records', ['assignment_id'], unique=True, postgresql_where=sa.text('assignment_id IS NOT NULL'))
    op.create_table('leave_request_shifts',
    sa.Column('leave_request_id', sa.UUID(), nullable=False),
    sa.Column('assignment_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['shift_assignments.id'], name=op.f('fk_leave_request_shifts_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['leave_request_id'], ['leave_requests.id'], name=op.f('fk_leave_request_shifts_leave_request_id_leave_requests'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('leave_request_id', 'assignment_id', name=op.f('pk_leave_request_shifts'))
    )
    op.create_table('opportunity_positions',
    sa.Column('opportunity_id', sa.UUID(), nullable=False),
    sa.Column('position_id', sa.UUID(), nullable=False),
    sa.Column('assignment_id', sa.UUID(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['shift_assignments.id'], name=op.f('fk_opportunity_positions_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['opportunity_id'], ['overtime_opportunities.id'], name=op.f('fk_opportunity_positions_opportunity_id_overtime_opportunities'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['position_id'], ['shift_positions.id'], name=op.f('fk_opportunity_positions_position_id_shift_positions')),
    sa.PrimaryKeyConstraint('opportunity_id', 'position_id', name=op.f('pk_opportunity_positions'))
    )
    op.create_table('overtime_claims',
    sa.Column('opportunity_id', sa.UUID(), nullable=False),
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('outcome', sa.Enum('won', 'lost', 'rejected', name='claim_outcome', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('reason_code', sa.String(length=40), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('assignment_id', sa.UUID(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['shift_assignments.id'], name=op.f('fk_overtime_claims_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_overtime_claims_employee_id_employees')),
    sa.ForeignKeyConstraint(['opportunity_id'], ['overtime_opportunities.id'], name=op.f('fk_overtime_claims_opportunity_id_overtime_opportunities')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_overtime_claims'))
    )
    op.create_index('ix_claims_employee', 'overtime_claims', ['employee_id', 'created_at'], unique=False)
    op.create_index('ix_claims_opportunity_created', 'overtime_claims', ['opportunity_id', 'created_at'], unique=False)
    op.create_table('workforce_conflicts',
    sa.Column('kind', sa.Enum('leave_cancelled_after_cover', 'withdrawal_request', name='conflict_kind', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('status', sa.Enum('open', 'resolved', name='conflict_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('department_id', sa.UUID(), nullable=False),
    sa.Column('shift_id', sa.UUID(), nullable=True),
    sa.Column('leave_request_id', sa.UUID(), nullable=True),
    sa.Column('original_assignment_id', sa.UUID(), nullable=True),
    sa.Column('replacement_assignment_id', sa.UUID(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('resolution', sa.String(length=40), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolved_by', sa.UUID(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_workforce_conflicts_department_id_departments')),
    sa.ForeignKeyConstraint(['leave_request_id'], ['leave_requests.id'], name=op.f('fk_workforce_conflicts_leave_request_id_leave_requests')),
    sa.ForeignKeyConstraint(['original_assignment_id'], ['shift_assignments.id'], name=op.f('fk_workforce_conflicts_original_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['replacement_assignment_id'], ['shift_assignments.id'], name=op.f('fk_workforce_conflicts_replacement_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('fk_workforce_conflicts_resolved_by_users')),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], name=op.f('fk_workforce_conflicts_shift_id_shifts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workforce_conflicts'))
    )
    op.create_index(op.f('ix_workforce_conflicts_status'), 'workforce_conflicts', ['status'], unique=False)
    op.create_table('attendance_corrections',
    sa.Column('employee_id', sa.UUID(), nullable=False),
    sa.Column('attendance_id', sa.UUID(), nullable=True),
    sa.Column('assignment_id', sa.UUID(), nullable=True),
    sa.Column('check_in_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('check_out_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('break_minutes', sa.Integer(), nullable=True),
    sa.Column('status_override', sa.Enum('present', 'absent', 'late', 'half_day', 'on_leave', 'holiday', 'weekly_off', name='correction_attendance_status', native_enum=False, create_constraint=True, length=30), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Enum('pending', 'approved', 'rejected', name='correction_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('requested_by', sa.UUID(), nullable=True),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['shift_assignments.id'], name=op.f('fk_attendance_corrections_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['attendance_id'], ['attendance_records.id'], name=op.f('fk_attendance_corrections_attendance_id_attendance_records')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_attendance_corrections_decided_by_users')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_attendance_corrections_employee_id_employees')),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_attendance_corrections_requested_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_corrections'))
    )
    op.create_index(op.f('ix_attendance_corrections_employee_id'), 'attendance_corrections', ['employee_id'], unique=False)
    op.create_index(op.f('ix_attendance_corrections_status'), 'attendance_corrections', ['status'], unique=False)
    # Foreign keys between tables that reference each other (Alembic ignores use_alter inside create_table).
    op.create_foreign_key(op.f('fk_employees_overtime_policy_id_overtime_policies'), 'employees', 'overtime_policies', ['overtime_policy_id'], ['id'])
    op.create_foreign_key(op.f('fk_employees_shift_pattern_id_shift_patterns'), 'employees', 'shift_patterns', ['shift_pattern_id'], ['id'])
    op.create_foreign_key(op.f('fk_shift_assignments_leave_request_id_leave_requests'), 'shift_assignments', 'leave_requests', ['leave_request_id'], ['id'])
    op.create_foreign_key(op.f('fk_shift_assignments_opportunity_id_overtime_opportunities'), 'shift_assignments', 'overtime_opportunities', ['opportunity_id'], ['id'])
    op.add_column('notification_preferences', sa.Column('push', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('notification_preferences', sa.Column('whatsapp', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('notifications', sa.Column('dedupe_key', sa.String(length=200), nullable=True))
    op.create_index('uq_notifications_dedupe_key', 'notifications', ['dedupe_key'], unique=True, postgresql_where=sa.text('dedupe_key IS NOT NULL'))


def downgrade() -> None:
    op.drop_constraint(op.f('fk_employees_overtime_policy_id_overtime_policies'), 'employees', type_='foreignkey')
    op.drop_constraint(op.f('fk_employees_shift_pattern_id_shift_patterns'), 'employees', type_='foreignkey')
    op.drop_constraint(op.f('fk_shift_assignments_leave_request_id_leave_requests'), 'shift_assignments', type_='foreignkey')
    op.drop_constraint(op.f('fk_shift_assignments_opportunity_id_overtime_opportunities'), 'shift_assignments', type_='foreignkey')
    op.drop_index('uq_notifications_dedupe_key', table_name='notifications', postgresql_where=sa.text('dedupe_key IS NOT NULL'))
    op.drop_column('notifications', 'dedupe_key')
    op.drop_column('notification_preferences', 'whatsapp')
    op.drop_column('notification_preferences', 'push')
    op.drop_index(op.f('ix_attendance_corrections_status'), table_name='attendance_corrections')
    op.drop_index(op.f('ix_attendance_corrections_employee_id'), table_name='attendance_corrections')
    op.drop_table('attendance_corrections')
    op.drop_index(op.f('ix_workforce_conflicts_status'), table_name='workforce_conflicts')
    op.drop_table('workforce_conflicts')
    op.drop_index('ix_claims_opportunity_created', table_name='overtime_claims')
    op.drop_index('ix_claims_employee', table_name='overtime_claims')
    op.drop_table('overtime_claims')
    op.drop_table('opportunity_positions')
    op.drop_table('leave_request_shifts')
    op.drop_index('uq_attendance_assignment', table_name='attendance_records', postgresql_where=sa.text('assignment_id IS NOT NULL'))
    op.drop_index('ix_attendance_employee_date', table_name='attendance_records')
    op.drop_table('attendance_records')
    op.drop_index('uq_assignments_active_position', table_name='shift_assignments', postgresql_where=sa.text("status = 'assigned'"))
    op.drop_index(op.f('ix_shift_assignments_shift_id'), table_name='shift_assignments')
    op.drop_index('ix_assignments_employee_window', table_name='shift_assignments')
    op.drop_table('shift_assignments')
    op.drop_table('opportunity_recipients')
    op.drop_table('shift_skills')
    op.drop_index(op.f('ix_shift_positions_shift_id'), table_name='shift_positions')
    op.drop_table('shift_positions')
    op.drop_index(op.f('ix_overtime_opportunities_shift_id'), table_name='overtime_opportunities')
    op.drop_index(op.f('ix_overtime_opportunities_department_id'), table_name='overtime_opportunities')
    op.drop_index('ix_opportunities_status_deadline', table_name='overtime_opportunities')
    op.drop_table('overtime_opportunities')
    op.drop_index('uq_shifts_template_date', table_name='shifts', postgresql_where=sa.text("template_id IS NOT NULL AND status = 'scheduled'"))
    op.drop_index('ix_shifts_window', table_name='shifts')
    op.drop_index(op.f('ix_shifts_shift_date'), table_name='shifts')
    op.drop_index(op.f('ix_shifts_department_id'), table_name='shifts')
    op.drop_table('shifts')
    op.drop_table('shift_template_skills')
    op.drop_table('shift_pattern_days')
    op.drop_index(op.f('ix_leave_approvals_leave_request_id'), table_name='leave_approvals')
    op.drop_table('leave_approvals')
    op.drop_table('team_members')
    op.drop_table('shift_templates')
    op.drop_index('ix_overtime_calc_status_date', table_name='overtime_calculations')
    op.drop_table('overtime_calculations')
    op.drop_index(op.f('ix_leave_requests_status'), table_name='leave_requests')
    op.drop_index('ix_leave_requests_employee_dates', table_name='leave_requests')
    op.drop_table('leave_requests')
    op.drop_table('leave_balances')
    op.drop_table('employee_skills')
    op.drop_index(op.f('ix_notification_deliveries_notification_id'), table_name='notification_deliveries')
    op.drop_index('ix_deliveries_pending', table_name='notification_deliveries')
    op.drop_table('notification_deliveries')
    op.drop_index('uq_employees_code_lower', table_name='employees')
    op.drop_index(op.f('ix_employees_manager_id'), table_name='employees')
    op.drop_index(op.f('ix_employees_department_id'), table_name='employees')
    op.drop_table('employees')
    op.drop_index('uq_workforce_policies_company', table_name='workforce_policies', postgresql_where=sa.text('department_id IS NULL'))
    op.drop_table('workforce_policies')
    op.drop_table('teams')
    op.drop_table('shift_patterns')
    op.drop_table('payroll_export_batches')
    op.drop_index('uq_overtime_policies_default', table_name='overtime_policies', postgresql_where=sa.text('is_default'))
    op.drop_table('overtime_policies')
    op.drop_table('leave_types')
    op.drop_index(op.f('ix_holidays_holiday_date'), table_name='holidays')
    op.drop_table('holidays')
    op.drop_table('designations')
    op.drop_table('work_locations')
    op.drop_table('skills')
