"""fase5_loan_payments_and_allocations

Revision ID: 006
Revises: 005
Create Date: 2026-11-06 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create loan_payments table (Immutable header + controlled POSTED -> REVERSED transition)
    op.create_table(
        'loan_payments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('payment_number', sa.String(length=24), nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('loan_payment_seq', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('request_payload_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='POSTED', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='MXN', nullable=False),
        sa.Column('amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('interest_applied_cents', sa.BigInteger(), nullable=False),
        sa.Column('principal_applied_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_loan_status', sa.String(length=25), nullable=False),
        sa.Column('prev_unpaid_principal_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_remaining_interest_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_remaining_total_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_amount_paid_cents', sa.BigInteger(), nullable=False),
        sa.Column('payment_method', sa.String(length=40), nullable=False),
        sa.Column('payment_reference', sa.String(length=120), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('recorded_by_user_id', sa.Integer(), nullable=False),
        sa.Column('reversed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reversal_reason', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('loan_payment_seq >= 1', name='ck_loan_payments_seq_positive'),
        sa.CheckConstraint("status IN ('POSTED', 'REVERSED')", name='ck_loan_payments_status_valid'),
        sa.CheckConstraint("currency = 'MXN'", name='ck_loan_payments_currency_mxn'),
        sa.CheckConstraint(
            "payment_method IN ('SPEI_TRANSFER', 'BANK_DEPOSIT', 'CHECK', 'CASH_RECEIPT')",
            name='ck_loan_payments_method_valid'
        ),
        sa.CheckConstraint(
            'amount_cents > 0 AND interest_applied_cents >= 0 AND principal_applied_cents >= 0',
            name='ck_loan_payments_amounts_positive'
        ),
        sa.CheckConstraint(
            'interest_applied_cents + principal_applied_cents = amount_cents',
            name='ck_loan_payments_components_sum'
        ),
        sa.CheckConstraint("prev_loan_status = 'ACTIVE'", name='ck_loan_payments_prev_status_active'),
        sa.CheckConstraint(
            'prev_unpaid_principal_cents >= 0 '
            'AND prev_remaining_interest_cents >= 0 '
            'AND prev_remaining_total_cents > 0 '
            'AND prev_amount_paid_cents >= 0 '
            'AND prev_unpaid_principal_cents + prev_remaining_interest_cents = prev_remaining_total_cents '
            'AND amount_cents <= prev_remaining_total_cents '
            'AND interest_applied_cents <= prev_remaining_interest_cents '
            'AND principal_applied_cents <= prev_unpaid_principal_cents',
            name='ck_loan_payments_prev_balances_valid'
        ),
        sa.CheckConstraint(
            "(status = 'POSTED' AND reversed_by_user_id IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL) "
            "OR (status = 'REVERSED' AND reversed_by_user_id IS NOT NULL AND reversed_at IS NOT NULL "
            "AND length(trim(reversal_reason)) >= 3)",
            name='ck_loan_payments_reversal_metadata'
        ),
        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['reversed_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_loan_payments_idempotency_key'),
        sa.UniqueConstraint('loan_id', 'loan_payment_seq', name='uq_loan_payments_loan_seq')
    )
    op.create_index(op.f('ix_loan_payments_id'), 'loan_payments', ['id'], unique=False)
    op.create_index(op.f('ix_loan_payments_payment_number'), 'loan_payments', ['payment_number'], unique=True)
    op.create_index(op.f('ix_loan_payments_loan_id'), 'loan_payments', ['loan_id'], unique=False)
    op.create_index(op.f('ix_loan_payments_customer_id'), 'loan_payments', ['customer_id'], unique=False)
    op.create_index(op.f('ix_loan_payments_status'), 'loan_payments', ['status'], unique=False)
    op.create_index(op.f('ix_loan_payments_paid_at'), 'loan_payments', ['paid_at'], unique=False)
    op.create_index(op.f('ix_loan_payments_payment_reference'), 'loan_payments', ['payment_reference'], unique=False)

    # 2. Create loan_payment_allocations table (Strictly append-only per-installment breakdown & snapshot)
    op.create_table(
        'loan_payment_allocations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=False),
        sa.Column('installment_id', sa.Integer(), nullable=False),
        sa.Column('installment_number', sa.Integer(), nullable=False),
        sa.Column('interest_allocated_cents', sa.BigInteger(), nullable=False),
        sa.Column('principal_allocated_cents', sa.BigInteger(), nullable=False),
        sa.Column('total_allocated_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_principal_paid_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_interest_paid_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_amount_paid_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_remaining_amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('prev_paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('installment_number >= 1', name='ck_payment_alloc_inst_num_pos'),
        sa.CheckConstraint(
            'total_allocated_cents > 0 AND interest_allocated_cents >= 0 AND principal_allocated_cents >= 0',
            name='ck_payment_alloc_amounts_valid'
        ),
        sa.CheckConstraint(
            'interest_allocated_cents + principal_allocated_cents = total_allocated_cents',
            name='ck_payment_alloc_components_sum'
        ),
        sa.CheckConstraint(
            'prev_principal_paid_cents >= 0 '
            'AND prev_interest_paid_cents >= 0 '
            'AND prev_amount_paid_cents >= 0 '
            'AND prev_remaining_amount_cents > 0 '
            'AND prev_remaining_amount_cents >= total_allocated_cents '
            'AND prev_principal_paid_cents + prev_interest_paid_cents = prev_amount_paid_cents '
            'AND prev_paid_at IS NULL',
            name='ck_payment_alloc_prev_valid'
        ),
        sa.ForeignKeyConstraint(['payment_id'], ['loan_payments.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['installment_id'], ['loan_installments.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('payment_id', 'installment_id', name='uq_payment_alloc_payment_inst')
    )
    op.create_index(op.f('ix_loan_payment_allocations_id'), 'loan_payment_allocations', ['id'], unique=False)
    op.create_index(op.f('ix_loan_payment_allocations_payment_id'), 'loan_payment_allocations', ['payment_id'], unique=False)
    op.create_index(op.f('ix_loan_payment_allocations_loan_id'), 'loan_payment_allocations', ['loan_id'], unique=False)
    op.create_index(op.f('ix_loan_payment_allocations_installment_id'), 'loan_payment_allocations', ['installment_id'], unique=False)

    # 3. PostgreSQL-specific constraint upgrades, partial index, and immutability/reconciliation triggers
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.drop_constraint('ck_loans_stage_balance_invariants', 'loans', type_='check')
        op.create_check_constraint(
            'ck_loans_stage_balance_invariants',
            'loans',
            "(status IN ('DRAFT', 'APPROVED', 'CANCELLED') "
            "AND unpaid_principal_cents = 0 "
            "AND remaining_contractual_interest_cents = 0 "
            "AND remaining_contract_total_cents = 0 "
            "AND amount_paid_cents = 0) "
            "OR (status = 'ACTIVE' "
            "AND unpaid_principal_cents >= 0 "
            "AND unpaid_principal_cents <= principal_cents "
            "AND remaining_contractual_interest_cents >= 0 "
            "AND remaining_contractual_interest_cents <= total_interest_cents "
            "AND remaining_contract_total_cents > 0 "
            "AND remaining_contract_total_cents <= total_repayment_cents "
            "AND amount_paid_cents >= 0 "
            "AND amount_paid_cents < total_repayment_cents "
            "AND amount_paid_cents + remaining_contract_total_cents = total_repayment_cents) "
            "OR (status = 'PAID_OFF' "
            "AND unpaid_principal_cents = 0 "
            "AND remaining_contractual_interest_cents = 0 "
            "AND remaining_contract_total_cents = 0 "
            "AND amount_paid_cents = total_repayment_cents) "
            "OR (status = 'DEFAULTED' "
            "AND remaining_contract_total_cents > 0 "
            "AND amount_paid_cents + remaining_contract_total_cents = total_repayment_cents)"
        )

        op.create_check_constraint(
            'ck_inst_paid_at_coherent',
            'loan_installments',
            '(remaining_amount_cents = 0 AND paid_at IS NOT NULL) '
            'OR (remaining_amount_cents > 0 AND paid_at IS NULL)'
        )

        op.execute("""
            CREATE UNIQUE INDEX uq_loan_payments_posted_method_ref
            ON loan_payments (payment_method, upper(btrim(payment_reference)))
            WHERE status = 'POSTED'
              AND payment_reference IS NOT NULL
              AND payment_method IN ('SPEI_TRANSFER', 'BANK_DEPOSIT', 'CHECK');
        """)

        op.execute("""
            CREATE OR REPLACE FUNCTION enforce_loans_immutability()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Physical deletion of loans is forbidden.';
                END IF;

                -- 1. Immutable contractual snapshot and identity columns
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.loan_number IS DISTINCT FROM OLD.loan_number
                   OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                   OR NEW.request_payload_hash IS DISTINCT FROM OLD.request_payload_hash
                   OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
                   OR NEW.source_lead_id IS DISTINCT FROM OLD.source_lead_id
                   OR NEW.product_name IS DISTINCT FROM OLD.product_name
                   OR NEW.product_version IS DISTINCT FROM OLD.product_version
                   OR NEW.currency IS DISTINCT FROM OLD.currency
                   OR NEW.principal_cents IS DISTINCT FROM OLD.principal_cents
                   OR NEW.monthly_interest_rate_bps IS DISTINCT FROM OLD.monthly_interest_rate_bps
                   OR NEW.interest_calculation_method IS DISTINCT FROM OLD.interest_calculation_method
                   OR NEW.interest_basis IS DISTINCT FROM OLD.interest_basis
                   OR NEW.interest_accrual_convention IS DISTINCT FROM OLD.interest_accrual_convention
                   OR NEW.assumed_interest_months IS DISTINCT FROM OLD.assumed_interest_months
                   OR NEW.payment_frequency IS DISTINCT FROM OLD.payment_frequency
                   OR NEW.term_installments_count IS DISTINCT FROM OLD.term_installments_count
                   OR NEW.rounding_policy IS DISTINCT FROM OLD.rounding_policy
                   OR NEW.contract_rules_json IS DISTINCT FROM OLD.contract_rules_json
                   OR NEW.monthly_interest_amount_cents IS DISTINCT FROM OLD.monthly_interest_amount_cents
                   OR NEW.total_interest_cents IS DISTINCT FROM OLD.total_interest_cents
                   OR NEW.total_repayment_cents IS DISTINCT FROM OLD.total_repayment_cents
                   OR NEW.regular_installment_cents IS DISTINCT FROM OLD.regular_installment_cents
                   OR NEW.final_installment_cents IS DISTINCT FROM OLD.final_installment_cents
                   OR NEW.start_date IS DISTINCT FROM OLD.start_date
                   OR NEW.first_payment_date IS DISTINCT FROM OLD.first_payment_date
                   OR NEW.maturity_date IS DISTINCT FROM OLD.maturity_date
                   OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION 'Immutable loan contractual snapshot or identity fields cannot be modified.';
                END IF;

                -- 2. Terminal state CANCELLED cannot be mutated
                IF OLD.status = 'CANCELLED' THEN
                    RAISE EXCEPTION 'Cancelled loans are terminal and cannot be modified.';
                END IF;

                -- 3. Controlled state transitions and operational balance invariants
                IF OLD.status = 'DRAFT' AND NEW.status = 'DRAFT' THEN
                    IF NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.approved_by_user_id IS NOT NULL
                       OR NEW.approved_at IS NOT NULL
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Invalid operational fields or balances while loan remains in DRAFT.';
                    END IF;

                ELSIF OLD.status = 'DRAFT' AND NEW.status = 'APPROVED' THEN
                    IF NEW.approved_by_user_id IS NULL
                       OR NEW.approved_at IS NULL
                       OR NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Transition DRAFT -> APPROVED requires approval metadata and zero active balances.';
                    END IF;

                ELSIF OLD.status = 'APPROVED' AND NEW.status = 'APPROVED' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Cannot alter approval metadata or balances while loan remains in APPROVED.';
                    END IF;

                ELSIF OLD.status IN ('DRAFT', 'APPROVED') AND NEW.status = 'CANCELLED' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.cancelled_by_user_id IS NULL
                       OR NEW.cancelled_at IS NULL
                       OR length(btrim(coalesce(NEW.cancellation_reason, ''))) < 3
                       OR NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Transition to CANCELLED requires cancellation metadata, reason, and zero balances.';
                    END IF;

                ELSIF OLD.status = 'APPROVED' AND NEW.status = 'ACTIVE' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.activated_by_user_id IS NULL
                       OR NEW.activated_at IS NULL
                       OR NEW.disbursed_at IS NULL
                       OR NEW.disbursement_method IS NULL
                       OR length(btrim(coalesce(NEW.disbursement_reference, ''))) < 3
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                       OR NEW.unpaid_principal_cents != OLD.principal_cents
                       OR NEW.remaining_contractual_interest_cents != OLD.total_interest_cents
                       OR NEW.remaining_contract_total_cents != OLD.total_repayment_cents
                       OR NEW.amount_paid_cents != 0
                    THEN
                        RAISE EXCEPTION 'Transition APPROVED -> ACTIVE requires disbursement confirmation and exact contractual opening balances.';
                    END IF;

                ELSIF OLD.status = 'ACTIVE' AND NEW.status = 'ACTIVE' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.activated_by_user_id IS DISTINCT FROM OLD.activated_by_user_id
                       OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
                       OR NEW.disbursed_at IS DISTINCT FROM OLD.disbursed_at
                       OR NEW.disbursement_method IS DISTINCT FROM OLD.disbursement_method
                       OR NEW.disbursement_reference IS DISTINCT FROM OLD.disbursement_reference
                       OR NEW.cancelled_by_user_id IS DISTINCT FROM OLD.cancelled_by_user_id
                       OR NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
                       OR NEW.remaining_contract_total_cents <= 0
                       OR NEW.unpaid_principal_cents < 0
                       OR NEW.unpaid_principal_cents > OLD.principal_cents
                       OR NEW.remaining_contractual_interest_cents < 0
                       OR NEW.remaining_contractual_interest_cents > OLD.total_interest_cents
                       OR NEW.unpaid_principal_cents + NEW.remaining_contractual_interest_cents != NEW.remaining_contract_total_cents
                       OR NEW.amount_paid_cents + NEW.remaining_contract_total_cents != OLD.total_repayment_cents
                    THEN
                        RAISE EXCEPTION 'Invalid disbursement metadata modification or incoherent balances on ACTIVE loan.';
                    END IF;

                ELSIF OLD.status = 'ACTIVE' AND NEW.status = 'PAID_OFF' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.activated_by_user_id IS DISTINCT FROM OLD.activated_by_user_id
                       OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
                       OR NEW.disbursed_at IS DISTINCT FROM OLD.disbursed_at
                       OR NEW.disbursement_method IS DISTINCT FROM OLD.disbursement_method
                       OR NEW.disbursement_reference IS DISTINCT FROM OLD.disbursement_reference
                       OR NEW.cancelled_by_user_id IS DISTINCT FROM OLD.cancelled_by_user_id
                       OR NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
                       OR NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != OLD.total_repayment_cents
                    THEN
                        RAISE EXCEPTION 'Transition ACTIVE -> PAID_OFF requires zero remaining balances and full contractual repayment.';
                    END IF;

                ELSIF OLD.status = 'PAID_OFF' AND NEW.status = 'ACTIVE' THEN
                    -- Controlled reversal from PAID_OFF -> ACTIVE: must match top-of-stack payment snapshot
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.activated_by_user_id IS DISTINCT FROM OLD.activated_by_user_id
                       OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
                       OR NEW.disbursed_at IS DISTINCT FROM OLD.disbursed_at
                       OR NEW.disbursement_method IS DISTINCT FROM OLD.disbursement_method
                       OR NEW.disbursement_reference IS DISTINCT FROM OLD.disbursement_reference
                       OR NEW.cancelled_by_user_id IS DISTINCT FROM OLD.cancelled_by_user_id
                       OR NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
                       OR NEW.remaining_contract_total_cents <= 0
                       OR NEW.unpaid_principal_cents + NEW.remaining_contractual_interest_cents != NEW.remaining_contract_total_cents
                       OR NEW.amount_paid_cents + NEW.remaining_contract_total_cents != OLD.total_repayment_cents
                    THEN
                        RAISE EXCEPTION 'Invalid metadata modification or balance arithmetic during PAID_OFF -> ACTIVE reversal.';
                    END IF;

                    IF NOT EXISTS (
                        SELECT 1 FROM loan_payments lp
                        WHERE lp.loan_id = OLD.id
                          AND lp.prev_loan_status = 'ACTIVE'
                          AND lp.prev_unpaid_principal_cents = NEW.unpaid_principal_cents
                          AND lp.prev_remaining_interest_cents = NEW.remaining_contractual_interest_cents
                          AND lp.prev_remaining_total_cents = NEW.remaining_contract_total_cents
                          AND lp.prev_amount_paid_cents = NEW.amount_paid_cents
                          AND lp.amount_cents = (OLD.amount_paid_cents - NEW.amount_paid_cents)
                          AND NOT EXISTS (
                              SELECT 1 FROM loan_payments lp_later
                              WHERE lp_later.loan_id = OLD.id
                                AND lp_later.status = 'POSTED'
                                AND lp_later.loan_payment_seq > lp.loan_payment_seq
                          )
                    ) THEN
                        RAISE EXCEPTION 'Forbidden PAID_OFF -> ACTIVE transition: no matching top-of-stack payment snapshot found.';
                    END IF;

                ELSE
                    RAISE EXCEPTION 'Invalid or forbidden loan status transition: % -> %', OLD.status, NEW.status;
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION enforce_loan_installments_immutability()
            RETURNS TRIGGER AS $$
            DECLARE
                v_loan_status VARCHAR(25);
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Physical deletion of loan installments is forbidden.';
                END IF;

                -- 1. Contractual schedule columns are strictly immutable
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.loan_id IS DISTINCT FROM OLD.loan_id
                   OR NEW.installment_number IS DISTINCT FROM OLD.installment_number
                   OR NEW.due_date IS DISTINCT FROM OLD.due_date
                   OR NEW.beginning_principal_cents IS DISTINCT FROM OLD.beginning_principal_cents
                   OR NEW.principal_due_cents IS DISTINCT FROM OLD.principal_due_cents
                   OR NEW.interest_due_cents IS DISTINCT FROM OLD.interest_due_cents
                   OR NEW.scheduled_payment_cents IS DISTINCT FROM OLD.scheduled_payment_cents
                   OR NEW.ending_principal_cents IS DISTINCT FROM OLD.ending_principal_cents
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION 'Immutable loan installment contractual schedule fields cannot be modified.';
                END IF;

                -- 2. Verify parent loan is in an operational payment/reversal state
                SELECT status INTO v_loan_status FROM loans WHERE id = OLD.loan_id;
                IF v_loan_status IS NULL OR v_loan_status NOT IN ('ACTIVE', 'PAID_OFF') THEN
                    RAISE EXCEPTION 'Installment payment balances can only be updated when parent loan is ACTIVE or PAID_OFF (current: %).', v_loan_status;
                END IF;

                -- 3. Verify installment accounting invariants
                IF NEW.principal_paid_cents < 0 OR NEW.principal_paid_cents > OLD.principal_due_cents
                   OR NEW.interest_paid_cents < 0 OR NEW.interest_paid_cents > OLD.interest_due_cents
                   OR NEW.principal_paid_cents + NEW.interest_paid_cents != NEW.amount_paid_cents
                   OR NEW.amount_paid_cents + NEW.remaining_amount_cents != OLD.scheduled_payment_cents
                THEN
                    RAISE EXCEPTION 'Installment payment components violate scheduled due bounds or sum invariants.';
                END IF;

                IF (NEW.remaining_amount_cents = 0 AND NEW.paid_at IS NULL)
                   OR (NEW.remaining_amount_cents > 0 AND NEW.paid_at IS NOT NULL)
                THEN
                    RAISE EXCEPTION 'Installment paid_at timestamp must be present iff remaining_amount_cents is zero.';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION enforce_loan_payments_immutability()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Physical deletion of loan_payments is forbidden.';
                END IF;

                IF OLD.status = 'REVERSED' THEN
                    RAISE EXCEPTION 'Reversed payments are terminal and cannot be modified.';
                END IF;

                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.payment_number IS DISTINCT FROM OLD.payment_number
                   OR NEW.loan_id IS DISTINCT FROM OLD.loan_id
                   OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
                   OR NEW.loan_payment_seq IS DISTINCT FROM OLD.loan_payment_seq
                   OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                   OR NEW.request_payload_hash IS DISTINCT FROM OLD.request_payload_hash
                   OR NEW.currency IS DISTINCT FROM OLD.currency
                   OR NEW.amount_cents IS DISTINCT FROM OLD.amount_cents
                   OR NEW.interest_applied_cents IS DISTINCT FROM OLD.interest_applied_cents
                   OR NEW.principal_applied_cents IS DISTINCT FROM OLD.principal_applied_cents
                   OR NEW.prev_loan_status IS DISTINCT FROM OLD.prev_loan_status
                   OR NEW.prev_unpaid_principal_cents IS DISTINCT FROM OLD.prev_unpaid_principal_cents
                   OR NEW.prev_remaining_interest_cents IS DISTINCT FROM OLD.prev_remaining_interest_cents
                   OR NEW.prev_remaining_total_cents IS DISTINCT FROM OLD.prev_remaining_total_cents
                   OR NEW.prev_amount_paid_cents IS DISTINCT FROM OLD.prev_amount_paid_cents
                   OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
                   OR NEW.payment_reference IS DISTINCT FROM OLD.payment_reference
                   OR NEW.paid_at IS DISTINCT FROM OLD.paid_at
                   OR NEW.notes IS DISTINCT FROM OLD.notes
                   OR NEW.recorded_by_user_id IS DISTINCT FROM OLD.recorded_by_user_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION 'Immutable payment ledger fields, amounts, effective dates, or historical snapshots cannot be modified.';
                END IF;

                IF NOT (OLD.status = 'POSTED' AND NEW.status = 'REVERSED') THEN
                    RAISE EXCEPTION 'Invalid payment status transition: only POSTED -> REVERSED is allowed.';
                END IF;

                IF NEW.reversed_by_user_id IS NULL
                   OR NEW.reversed_at IS NULL
                   OR length(btrim(coalesce(NEW.reversal_reason, ''))) < 3
                THEN
                    RAISE EXCEPTION 'Payment reversal requires reversed_by_user_id, reversed_at, and a reversal_reason of at least 3 characters.';
                END IF;

                -- LIFO enforcement: cannot reverse if a newer POSTED payment exists for the same loan
                IF EXISTS (
                    SELECT 1 FROM loan_payments lp
                    WHERE lp.loan_id = OLD.loan_id
                      AND lp.status = 'POSTED'
                      AND lp.loan_payment_seq > OLD.loan_payment_seq
                ) THEN
                    RAISE EXCEPTION 'LIFO violation: only the most recent POSTED payment in registration order can be reversed.';
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_protect_loan_payments_immutability
            BEFORE UPDATE OR DELETE ON loan_payments
            FOR EACH ROW EXECUTE FUNCTION enforce_loan_payments_immutability();

            CREATE TRIGGER trg_protect_loan_payments_stmt
            BEFORE TRUNCATE ON loan_payments
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_loans_truncate();

            CREATE OR REPLACE FUNCTION enforce_loan_payment_allocations_immutability()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'Physical modification or deletion of loan_payment_allocations is forbidden. Allocations are strictly immutable.';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_protect_loan_payment_allocations_immutability
            BEFORE UPDATE OR DELETE ON loan_payment_allocations
            FOR EACH ROW EXECUTE FUNCTION enforce_loan_payment_allocations_immutability();

            CREATE TRIGGER trg_protect_loan_payment_allocations_stmt
            BEFORE TRUNCATE ON loan_payment_allocations
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_loans_truncate();

            -- 4. Deferred cross-table accounting reconciliation at COMMIT time
            CREATE OR REPLACE FUNCTION assert_loan_ledger_integrity(p_loan_id INTEGER)
            RETURNS VOID AS $$
            DECLARE
                v_loan RECORD;
                v_posted_count INTEGER;
                v_posted_amount BIGINT;
                v_posted_int BIGINT;
                v_posted_prin BIGINT;
                v_inst_paid BIGINT;
                v_inst_int_paid BIGINT;
                v_inst_prin_paid BIGINT;
                v_inst_rem BIGINT;
            BEGIN
                SELECT * INTO v_loan FROM loans WHERE id = p_loan_id;
                IF NOT FOUND THEN
                    RETURN;
                END IF;

                SELECT COUNT(*),
                       COALESCE(SUM(amount_cents), 0),
                       COALESCE(SUM(interest_applied_cents), 0),
                       COALESCE(SUM(principal_applied_cents), 0)
                  INTO v_posted_count, v_posted_amount, v_posted_int, v_posted_prin
                  FROM loan_payments
                 WHERE loan_id = p_loan_id AND status = 'POSTED';

                SELECT COALESCE(SUM(amount_paid_cents), 0),
                       COALESCE(SUM(interest_paid_cents), 0),
                       COALESCE(SUM(principal_paid_cents), 0),
                       COALESCE(SUM(remaining_amount_cents), 0)
                  INTO v_inst_paid, v_inst_int_paid, v_inst_prin_paid, v_inst_rem
                  FROM loan_installments
                 WHERE loan_id = p_loan_id;

                IF v_loan.status IN ('DRAFT', 'APPROVED', 'CANCELLED') THEN
                    IF v_posted_count != 0 OR v_inst_paid != 0 OR v_inst_int_paid != 0 OR v_inst_prin_paid != 0 THEN
                        RAISE EXCEPTION 'Ledger integrity violation: non-active loan % in status % cannot have posted payments or paid installments.', p_loan_id, v_loan.status;
                    END IF;
                ELSIF v_loan.status IN ('ACTIVE', 'PAID_OFF', 'DEFAULTED') THEN
                    IF v_loan.amount_paid_cents != v_posted_amount OR v_loan.amount_paid_cents != v_inst_paid THEN
                        RAISE EXCEPTION 'Ledger integrity violation on loan %: loan.amount_paid_cents (%) != posted_payments (%) or installments_paid (%).',
                            p_loan_id, v_loan.amount_paid_cents, v_posted_amount, v_inst_paid;
                    END IF;

                    IF (v_loan.principal_cents - v_loan.unpaid_principal_cents) != v_posted_principal
                       OR (v_loan.principal_cents - v_loan.unpaid_principal_cents) != v_inst_prin_paid THEN
                        RAISE EXCEPTION 'Ledger integrity violation on loan %: principal paid mismatch across loan, payments, and installments.', p_loan_id;
                    END IF;

                    IF (v_loan.total_interest_cents - v_loan.remaining_contractual_interest_cents) != v_posted_int
                       OR (v_loan.total_interest_cents - v_loan.remaining_contractual_interest_cents) != v_inst_int_paid THEN
                        RAISE EXCEPTION 'Ledger integrity violation on loan %: interest paid mismatch across loan, payments, and installments.', p_loan_id;
                    END IF;

                    IF v_loan.remaining_contract_total_cents != v_inst_rem THEN
                        RAISE EXCEPTION 'Ledger integrity violation on loan %: loan.remaining_contract_total_cents (%) != installments remaining (%).',
                            p_loan_id, v_loan.remaining_contract_total_cents, v_inst_rem;
                    END IF;
                END IF;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION trg_fn_verify_loans_ledger()
            RETURNS TRIGGER AS $$
            BEGIN
                PERFORM assert_loan_ledger_integrity(NEW.id);
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION trg_fn_verify_installments_ledger()
            RETURNS TRIGGER AS $$
            BEGIN
                PERFORM assert_loan_ledger_integrity(NEW.loan_id);
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION trg_fn_verify_payments_ledger()
            RETURNS TRIGGER AS $$
            DECLARE
                v_alloc_count INTEGER;
                v_alloc_int BIGINT;
                v_alloc_prin BIGINT;
                v_alloc_tot BIGINT;
            BEGIN
                SELECT COUNT(*),
                       COALESCE(SUM(interest_allocated_cents), 0),
                       COALESCE(SUM(principal_allocated_cents), 0),
                       COALESCE(SUM(total_allocated_cents), 0)
                  INTO v_alloc_count, v_alloc_int, v_alloc_prin, v_alloc_tot
                  FROM loan_payment_allocations
                 WHERE payment_id = NEW.id AND loan_id = NEW.loan_id;

                IF v_alloc_count < 1
                   OR v_alloc_int != NEW.interest_applied_cents
                   OR v_alloc_prin != NEW.principal_applied_cents
                   OR v_alloc_tot != NEW.amount_cents
                THEN
                    RAISE EXCEPTION 'Payment allocation integrity violation on payment %: allocations sum (int=%, prin=%, tot=%) does not match payment header (int=%, prin=%, tot=%).',
                        NEW.id, v_alloc_int, v_alloc_prin, v_alloc_tot, NEW.interest_applied_cents, NEW.principal_applied_cents, NEW.amount_cents;
                END IF;

                PERFORM assert_loan_ledger_integrity(NEW.loan_id);
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;

            CREATE CONSTRAINT TRIGGER trg_verify_loans_ledger_consistency
            AFTER UPDATE ON loans
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION trg_fn_verify_loans_ledger();

            CREATE CONSTRAINT TRIGGER trg_verify_installments_ledger_consistency
            AFTER UPDATE ON loan_installments
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION trg_fn_verify_installments_ledger();

            CREATE CONSTRAINT TRIGGER trg_verify_payments_ledger_consistency
            AFTER INSERT OR UPDATE ON loan_payments
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION trg_fn_verify_payments_ledger();
        """)


def downgrade() -> None:
    bind = op.get_bind()

    # Safety Guard: Never allow destructive downgrade if any payment, allocation, or paid balance exists
    payments_count = bind.execute(sa.text("SELECT COUNT(*) FROM loan_payments")).scalar() or 0
    allocations_count = bind.execute(sa.text("SELECT COUNT(*) FROM loan_payment_allocations")).scalar() or 0
    modified_loans_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM loans WHERE amount_paid_cents > 0 OR status = 'PAID_OFF'")
    ).scalar() or 0
    modified_inst_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM loan_installments WHERE amount_paid_cents > 0 OR paid_at IS NOT NULL")
    ).scalar() or 0

    if payments_count > 0 or allocations_count > 0 or modified_loans_count > 0 or modified_inst_count > 0:
        raise RuntimeError(
            "ABORTING DOWNGRADE 006 -> 005: Active financial payment records or modified balances exist "
            f"(payments={payments_count}, allocations={allocations_count}, "
            f"paid_loans={modified_loans_count}, paid_installments={modified_inst_count}). "
            "Destructive downgrade of the financial ledger is strictly forbidden."
        )

    if bind.dialect.name == 'postgresql':
        op.execute("DROP TRIGGER IF EXISTS trg_verify_payments_ledger_consistency ON loan_payments;")
        op.execute("DROP TRIGGER IF EXISTS trg_verify_installments_ledger_consistency ON loan_installments;")
        op.execute("DROP TRIGGER IF EXISTS trg_verify_loans_ledger_consistency ON loans;")
        op.execute("DROP FUNCTION IF EXISTS trg_fn_verify_payments_ledger();")
        op.execute("DROP FUNCTION IF EXISTS trg_fn_verify_installments_ledger();")
        op.execute("DROP FUNCTION IF EXISTS trg_fn_verify_loans_ledger();")
        op.execute("DROP FUNCTION IF EXISTS assert_loan_ledger_integrity(INTEGER);")

        op.execute("DROP TRIGGER IF EXISTS trg_protect_loan_payment_allocations_stmt ON loan_payment_allocations;")
        op.execute("DROP TRIGGER IF EXISTS trg_protect_loan_payment_allocations_immutability ON loan_payment_allocations;")
        op.execute("DROP FUNCTION IF EXISTS enforce_loan_payment_allocations_immutability();")

        op.execute("DROP TRIGGER IF EXISTS trg_protect_loan_payments_stmt ON loan_payments;")
        op.execute("DROP TRIGGER IF EXISTS trg_protect_loan_payments_immutability ON loan_payments;")
        op.execute("DROP FUNCTION IF EXISTS enforce_loan_payments_immutability();")

        op.execute("DROP INDEX IF EXISTS uq_loan_payments_posted_method_ref;")

        op.drop_constraint('ck_inst_paid_at_coherent', 'loan_installments', type_='check')

        op.drop_constraint('ck_loans_stage_balance_invariants', 'loans', type_='check')
        op.create_check_constraint(
            'ck_loans_stage_balance_invariants',
            'loans',
            "(status IN ('DRAFT', 'APPROVED', 'CANCELLED') "
            "AND unpaid_principal_cents = 0 "
            "AND remaining_contractual_interest_cents = 0 "
            "AND remaining_contract_total_cents = 0 "
            "AND amount_paid_cents = 0) "
            "OR (status = 'ACTIVE' "
            "AND unpaid_principal_cents = principal_cents "
            "AND remaining_contractual_interest_cents = total_interest_cents "
            "AND remaining_contract_total_cents = total_repayment_cents "
            "AND amount_paid_cents = 0) "
            "OR (status IN ('PAID_OFF', 'DEFAULTED'))"
        )

        # Restore Stage 3/4 trigger functions from 005
        op.execute("""
            CREATE OR REPLACE FUNCTION enforce_loans_immutability()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Physical deletion of loans is forbidden.';
                END IF;

                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.loan_number IS DISTINCT FROM OLD.loan_number
                   OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                   OR NEW.request_payload_hash IS DISTINCT FROM OLD.request_payload_hash
                   OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
                   OR NEW.source_lead_id IS DISTINCT FROM OLD.source_lead_id
                   OR NEW.product_name IS DISTINCT FROM OLD.product_name
                   OR NEW.product_version IS DISTINCT FROM OLD.product_version
                   OR NEW.currency IS DISTINCT FROM OLD.currency
                   OR NEW.principal_cents IS DISTINCT FROM OLD.principal_cents
                   OR NEW.monthly_interest_rate_bps IS DISTINCT FROM OLD.monthly_interest_rate_bps
                   OR NEW.interest_calculation_method IS DISTINCT FROM OLD.interest_calculation_method
                   OR NEW.interest_basis IS DISTINCT FROM OLD.interest_basis
                   OR NEW.interest_accrual_convention IS DISTINCT FROM OLD.interest_accrual_convention
                   OR NEW.assumed_interest_months IS DISTINCT FROM OLD.assumed_interest_months
                   OR NEW.payment_frequency IS DISTINCT FROM OLD.payment_frequency
                   OR NEW.term_installments_count IS DISTINCT FROM OLD.term_installments_count
                   OR NEW.rounding_policy IS DISTINCT FROM OLD.rounding_policy
                   OR NEW.contract_rules_json IS DISTINCT FROM OLD.contract_rules_json
                   OR NEW.monthly_interest_amount_cents IS DISTINCT FROM OLD.monthly_interest_amount_cents
                   OR NEW.total_interest_cents IS DISTINCT FROM OLD.total_interest_cents
                   OR NEW.total_repayment_cents IS DISTINCT FROM OLD.total_repayment_cents
                   OR NEW.regular_installment_cents IS DISTINCT FROM OLD.regular_installment_cents
                   OR NEW.final_installment_cents IS DISTINCT FROM OLD.final_installment_cents
                   OR NEW.start_date IS DISTINCT FROM OLD.start_date
                   OR NEW.first_payment_date IS DISTINCT FROM OLD.first_payment_date
                   OR NEW.maturity_date IS DISTINCT FROM OLD.maturity_date
                   OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION 'Immutable loan contractual snapshot or identity fields cannot be modified.';
                END IF;

                IF OLD.status = 'CANCELLED' THEN
                    RAISE EXCEPTION 'Cancelled loans are terminal and cannot be modified.';
                END IF;

                IF OLD.status = 'DRAFT' AND NEW.status = 'DRAFT' THEN
                    IF NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.approved_by_user_id IS NOT NULL
                       OR NEW.approved_at IS NOT NULL
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Invalid operational fields or balances while loan remains in DRAFT.';
                    END IF;
                ELSIF OLD.status = 'DRAFT' AND NEW.status = 'APPROVED' THEN
                    IF NEW.approved_by_user_id IS NULL
                       OR NEW.approved_at IS NULL
                       OR NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Transition DRAFT -> APPROVED requires approval metadata and zero active balances.';
                    END IF;
                ELSIF OLD.status = 'APPROVED' AND NEW.status = 'APPROVED' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Cannot alter approval metadata or balances while loan remains in APPROVED.';
                    END IF;
                ELSIF OLD.status IN ('DRAFT', 'APPROVED') AND NEW.status = 'CANCELLED' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.cancelled_by_user_id IS NULL
                       OR NEW.cancelled_at IS NULL
                       OR length(btrim(coalesce(NEW.cancellation_reason, ''))) < 3
                       OR NEW.unpaid_principal_cents != 0
                       OR NEW.remaining_contractual_interest_cents != 0
                       OR NEW.remaining_contract_total_cents != 0
                       OR NEW.amount_paid_cents != 0
                       OR NEW.activated_by_user_id IS NOT NULL
                       OR NEW.activated_at IS NOT NULL
                       OR NEW.disbursed_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION 'Transition to CANCELLED requires cancellation metadata, reason, and zero balances.';
                    END IF;
                ELSIF OLD.status = 'APPROVED' AND NEW.status = 'ACTIVE' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.activated_by_user_id IS NULL
                       OR NEW.activated_at IS NULL
                       OR NEW.disbursed_at IS NULL
                       OR NEW.disbursement_method IS NULL
                       OR length(btrim(coalesce(NEW.disbursement_reference, ''))) < 3
                       OR NEW.cancelled_by_user_id IS NOT NULL
                       OR NEW.cancelled_at IS NOT NULL
                       OR NEW.unpaid_principal_cents != OLD.principal_cents
                       OR NEW.remaining_contractual_interest_cents != OLD.total_interest_cents
                       OR NEW.remaining_contract_total_cents != OLD.total_repayment_cents
                       OR NEW.amount_paid_cents != 0
                    THEN
                        RAISE EXCEPTION 'Transition APPROVED -> ACTIVE requires disbursement confirmation and exact contractual opening balances.';
                    END IF;
                ELSIF OLD.status = 'ACTIVE' AND NEW.status = 'ACTIVE' THEN
                    IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
                       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
                       OR NEW.activated_by_user_id IS DISTINCT FROM OLD.activated_by_user_id
                       OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
                       OR NEW.disbursed_at IS DISTINCT FROM OLD.disbursed_at
                       OR NEW.disbursement_method IS DISTINCT FROM OLD.disbursement_method
                       OR NEW.disbursement_reference IS DISTINCT FROM OLD.disbursement_reference
                       OR NEW.cancelled_by_user_id IS DISTINCT FROM OLD.cancelled_by_user_id
                       OR NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
                       OR NEW.unpaid_principal_cents IS DISTINCT FROM OLD.unpaid_principal_cents
                       OR NEW.remaining_contractual_interest_cents IS DISTINCT FROM OLD.remaining_contractual_interest_cents
                       OR NEW.remaining_contract_total_cents IS DISTINCT FROM OLD.remaining_contract_total_cents
                       OR NEW.amount_paid_cents IS DISTINCT FROM OLD.amount_paid_cents
                    THEN
                        RAISE EXCEPTION 'Active loan disbursement metadata and balances cannot be modified in Stage 3.';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'Invalid or forbidden loan status transition: % -> %', OLD.status, NEW.status;
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION enforce_loan_installments_immutability()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Physical deletion of loan installments is forbidden.';
                END IF;
                RAISE EXCEPTION 'Modification of loan installments is forbidden in Stage 3 (Payments ledger not yet enabled).';
            END;
            $$ LANGUAGE plpgsql;
        """)

    op.drop_index(op.f('ix_loan_payment_allocations_installment_id'), table_name='loan_payment_allocations')
    op.drop_index(op.f('ix_loan_payment_allocations_loan_id'), table_name='loan_payment_allocations')
    op.drop_index(op.f('ix_loan_payment_allocations_payment_id'), table_name='loan_payment_allocations')
    op.drop_index(op.f('ix_loan_payment_allocations_id'), table_name='loan_payment_allocations')
    op.drop_table('loan_payment_allocations')

    op.drop_index(op.f('ix_loan_payments_payment_reference'), table_name='loan_payments')
    op.drop_index(op.f('ix_loan_payments_paid_at'), table_name='loan_payments')
    op.drop_index(op.f('ix_loan_payments_status'), table_name='loan_payments')
    op.drop_index(op.f('ix_loan_payments_customer_id'), table_name='loan_payments')
    op.drop_index(op.f('ix_loan_payments_loan_id'), table_name='loan_payments')
    op.drop_index(op.f('ix_loan_payments_payment_number'), table_name='loan_payments')
    op.drop_index(op.f('ix_loan_payments_id'), table_name='loan_payments')
    op.drop_table('loan_payments')
