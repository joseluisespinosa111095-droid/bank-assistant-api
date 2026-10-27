"""fase4_loans_and_installments

Revision ID: 005
Revises: 004
Create Date: 2026-10-26 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'loans',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('loan_number', sa.String(length=24), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('request_payload_hash', sa.String(length=64), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('source_lead_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=25), server_default='DRAFT', nullable=False),
        sa.Column('product_name', sa.String(length=60), nullable=False),
        sa.Column('product_version', sa.String(length=20), server_default='v1.0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='MXN', nullable=False),
        sa.Column('principal_cents', sa.BigInteger(), nullable=False),
        sa.Column('monthly_interest_rate_bps', sa.Integer(), nullable=False),
        sa.Column('interest_calculation_method', sa.String(length=40), server_default='FLAT_ORIGINAL_PRINCIPAL', nullable=False),
        sa.Column('interest_basis', sa.String(length=40), server_default='ORIGINAL_PRINCIPAL', nullable=False),
        sa.Column('interest_accrual_convention', sa.String(length=40), server_default='ON_INSTALLMENT_DUE_DATE', nullable=False),
        sa.Column('assumed_interest_months', sa.Integer(), nullable=False),
        sa.Column('payment_frequency', sa.String(length=20), server_default='WEEKLY', nullable=False),
        sa.Column('term_installments_count', sa.Integer(), nullable=False),
        sa.Column('rounding_policy', sa.String(length=40), server_default='FLOOR_REGULAR_EXACT_FINAL', nullable=False),
        sa.Column('contract_rules_json', sa.Text(), nullable=False),
        sa.Column('monthly_interest_amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('total_interest_cents', sa.BigInteger(), nullable=False),
        sa.Column('total_repayment_cents', sa.BigInteger(), nullable=False),
        sa.Column('regular_installment_cents', sa.BigInteger(), nullable=False),
        sa.Column('final_installment_cents', sa.BigInteger(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('first_payment_date', sa.Date(), nullable=False),
        sa.Column('maturity_date', sa.Date(), nullable=False),
        sa.Column('unpaid_principal_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('remaining_contractual_interest_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('remaining_contract_total_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('amount_paid_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('approved_by_user_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approval_notes', sa.Text(), nullable=True),
        sa.Column('activated_by_user_id', sa.Integer(), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disbursed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disbursement_method', sa.String(length=40), nullable=True),
        sa.Column('disbursement_reference', sa.String(length=120), nullable=True),
        sa.Column('disbursement_notes', sa.Text(), nullable=True),
        sa.Column('cancelled_by_user_id', sa.Integer(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancellation_reason', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'ACTIVE', 'PAID_OFF', 'CANCELLED', 'DEFAULTED')",
            name='ck_loans_status_valid'
        ),
        sa.CheckConstraint('principal_cents > 0', name='ck_loans_principal_positive'),
        sa.CheckConstraint('monthly_interest_rate_bps >= 0', name='ck_loans_rate_non_negative'),
        sa.CheckConstraint(
            'term_installments_count >= 1 AND assumed_interest_months >= 1',
            name='ck_loans_term_positive'
        ),
        sa.CheckConstraint(
            'principal_cents + total_interest_cents = total_repayment_cents',
            name='ck_loans_contract_totals_coherent'
        ),
        sa.CheckConstraint(
            'unpaid_principal_cents + remaining_contractual_interest_cents = remaining_contract_total_cents',
            name='ck_loans_remaining_totals_coherent'
        ),
        sa.CheckConstraint(
            'unpaid_principal_cents >= 0 AND remaining_contractual_interest_cents >= 0 '
            'AND remaining_contract_total_cents >= 0 AND amount_paid_cents >= 0',
            name='ck_loans_non_negative_balances'
        ),
        sa.CheckConstraint(
            'first_payment_date >= start_date AND maturity_date >= first_payment_date',
            name='ck_loans_dates_ordered'
        ),
        sa.CheckConstraint(
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
            "OR (status IN ('PAID_OFF', 'DEFAULTED'))",
            name='ck_loans_stage_balance_invariants'
        ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['source_lead_id'], ['leads.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['activated_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['cancelled_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_loans_idempotency_key')
    )
    op.create_index(op.f('ix_loans_id'), 'loans', ['id'], unique=False)
    op.create_index(op.f('ix_loans_loan_number'), 'loans', ['loan_number'], unique=True)
    op.create_index(op.f('ix_loans_customer_id'), 'loans', ['customer_id'], unique=False)
    op.create_index(op.f('ix_loans_source_lead_id'), 'loans', ['source_lead_id'], unique=False)
    op.create_index(op.f('ix_loans_status'), 'loans', ['status'], unique=False)
    op.create_index(op.f('ix_loans_owner_user_id'), 'loans', ['owner_user_id'], unique=False)
    op.create_index(op.f('ix_loans_first_payment_date'), 'loans', ['first_payment_date'], unique=False)
    op.create_index(op.f('ix_loans_maturity_date'), 'loans', ['maturity_date'], unique=False)

    op.create_table(
        'loan_installments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=False),
        sa.Column('installment_number', sa.Integer(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('beginning_principal_cents', sa.BigInteger(), nullable=False),
        sa.Column('principal_due_cents', sa.BigInteger(), nullable=False),
        sa.Column('interest_due_cents', sa.BigInteger(), nullable=False),
        sa.Column('scheduled_payment_cents', sa.BigInteger(), nullable=False),
        sa.Column('ending_principal_cents', sa.BigInteger(), nullable=False),
        sa.Column('principal_paid_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('interest_paid_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('amount_paid_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('remaining_amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('installment_number >= 1', name='ck_inst_number_positive'),
        sa.CheckConstraint(
            'principal_due_cents + interest_due_cents = scheduled_payment_cents',
            name='ck_inst_components_sum'
        ),
        sa.CheckConstraint(
            'beginning_principal_cents - principal_due_cents = ending_principal_cents',
            name='ck_inst_principal_roll'
        ),
        sa.CheckConstraint(
            'principal_paid_cents + interest_paid_cents = amount_paid_cents',
            name='ck_inst_paid_components_sum'
        ),
        sa.CheckConstraint(
            'amount_paid_cents + remaining_amount_cents = scheduled_payment_cents',
            name='ck_inst_remaining_balance'
        ),
        sa.CheckConstraint(
            'beginning_principal_cents >= 0 AND principal_due_cents >= 0 '
            'AND interest_due_cents >= 0 AND scheduled_payment_cents >= 0 '
            'AND ending_principal_cents >= 0 AND principal_paid_cents >= 0 '
            'AND interest_paid_cents >= 0 AND amount_paid_cents >= 0 '
            'AND remaining_amount_cents >= 0',
            name='ck_inst_non_negative'
        ),
        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('loan_id', 'installment_number', name='uq_loan_installments_loan_num')
    )
    op.create_index(op.f('ix_loan_installments_id'), 'loan_installments', ['id'], unique=False)
    op.create_index(op.f('ix_loan_installments_loan_id'), 'loan_installments', ['loan_id'], unique=False)
    op.create_index(op.f('ix_loan_installments_due_date'), 'loan_installments', ['due_date'], unique=False)

    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("""
            CREATE UNIQUE INDEX uq_loans_disbursement_method_ref
            ON loans (disbursement_method, disbursement_reference)
            WHERE disbursement_method IS NOT NULL AND disbursement_reference IS NOT NULL;
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

            CREATE TRIGGER trg_protect_loans_immutability
            BEFORE UPDATE OR DELETE ON loans
            FOR EACH ROW EXECUTE FUNCTION enforce_loans_immutability();

            CREATE OR REPLACE FUNCTION prevent_loans_truncate()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'TRUNCATE on loans or loan_installments is forbidden.';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_protect_loans_stmt
            BEFORE TRUNCATE ON loans
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_loans_truncate();

            CREATE OR REPLACE FUNCTION enforce_loan_installments_immutability()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Physical deletion of loan installments is forbidden.';
                END IF;
                RAISE EXCEPTION 'Modification of loan installments is forbidden in Stage 3 (Payments ledger not yet enabled).';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_protect_loan_installments_immutability
            BEFORE UPDATE OR DELETE ON loan_installments
            FOR EACH ROW EXECUTE FUNCTION enforce_loan_installments_immutability();

            CREATE TRIGGER trg_protect_loan_installments_stmt
            BEFORE TRUNCATE ON loan_installments
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_loans_truncate();
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("DROP TRIGGER IF EXISTS trg_protect_loan_installments_stmt ON loan_installments;")
        op.execute("DROP TRIGGER IF EXISTS trg_protect_loan_installments_immutability ON loan_installments;")
        op.execute("DROP FUNCTION IF EXISTS enforce_loan_installments_immutability();")
        op.execute("DROP TRIGGER IF EXISTS trg_protect_loans_stmt ON loans;")
        op.execute("DROP TRIGGER IF EXISTS trg_protect_loans_immutability ON loans;")
        op.execute("DROP FUNCTION IF EXISTS prevent_loans_truncate();")
        op.execute("DROP FUNCTION IF EXISTS enforce_loans_immutability();")
        op.execute("DROP INDEX IF EXISTS uq_loans_disbursement_method_ref;")

    op.drop_index(op.f('ix_loan_installments_due_date'), table_name='loan_installments')
    op.drop_index(op.f('ix_loan_installments_loan_id'), table_name='loan_installments')
    op.drop_index(op.f('ix_loan_installments_id'), table_name='loan_installments')
    op.drop_table('loan_installments')

    op.drop_index(op.f('ix_loans_maturity_date'), table_name='loans')
    op.drop_index(op.f('ix_loans_first_payment_date'), table_name='loans')
    op.drop_index(op.f('ix_loans_owner_user_id'), table_name='loans')
    op.drop_index(op.f('ix_loans_status'), table_name='loans')
    op.drop_index(op.f('ix_loans_source_lead_id'), table_name='loans')
    op.drop_index(op.f('ix_loans_customer_id'), table_name='loans')
    op.drop_index(op.f('ix_loans_loan_number'), table_name='loans')
    op.drop_index(op.f('ix_loans_id'), table_name='loans')
    op.drop_table('loans')
