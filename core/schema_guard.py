from typing import Optional

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

import db_config
from core.config import EXPECTED_ALEMBIC_REVISION, IS_PRODUCTION, logger

# --- STARTUP SCHEMA GUARD ---
_AUTH_SECURITY_SCHEMA_READY = False


def is_auth_security_schema_ready(db: Optional[Session] = None) -> bool:
    global _AUTH_SECURITY_SCHEMA_READY
    if _AUTH_SECURITY_SCHEMA_READY:
        return True
    target_bind = db.get_bind() if db is not None else db_config.engine
    try:
        tables = set(inspect(target_bind).get_table_names())
        if {"rate_limits", "password_reset_tokens"}.issubset(tables):
            _AUTH_SECURITY_SCHEMA_READY = True
            return True
    except Exception:
        pass
    return False


def verify_database_schema_compatibility(db_engine):
    global _AUTH_SECURITY_SCHEMA_READY
    if not IS_PRODUCTION and "sqlite" in str(db_engine.url):
        is_auth_security_schema_ready()
        return  # SQLite dev DB trusts test fixtures / alembic
    try:
        inspector = inspect(db_engine)
        tables = set(inspector.get_table_names())
        base_required_tables = {
            "tenants", "tenant_sequences",
            "platform_modules", "platform_module_dependencies", "tenant_modules",
            "users", "sessions", "conversations", "chat_messages", "audit_logs",
            "customers", "customer_documents", "leads", "lead_stage_history",
            "interactions", "loans", "loan_installments",
            "loan_payments", "loan_payment_allocations",
            "rate_limits", "password_reset_tokens"
        }
        if not base_required_tables.issubset(tables):
            missing = base_required_tables - tables
            raise RuntimeError(f"Database schema is missing required tables: {missing}")

        with db_engine.begin() as conn:
            res = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            if not res:
                raise RuntimeError("No alembic_version record found in database.")
            rev = res[0]
            if rev != EXPECTED_ALEMBIC_REVISION:
                raise RuntimeError(
                    f"Database schema is at revision '{rev}', expected '{EXPECTED_ALEMBIC_REVISION}'. Run 'alembic upgrade head'."
                )
            _AUTH_SECURITY_SCHEMA_READY = True

            if db_engine.dialect.name == "postgresql":
                conn.execute(text("""
                    DO $$
                    DECLARE
                        r RECORD;
                    BEGIN
                        FOR r IN
                            SELECT conname, pg_get_constraintdef(oid) AS def
                            FROM pg_constraint
                            WHERE conrelid = 'loans'::regclass AND contype = 'c'
                        LOOP
                            IF r.def ILIKE '%FLAT_ORIGINAL_PRINCIPAL%'
                               OR r.def ILIKE '%ORIGINAL_PRINCIPAL%'
                               OR r.def ILIKE '%FLOOR_REGULAR_EXACT_FINAL%'
                               OR r.def ILIKE '%monthly_interest_amount_cents%'
                               OR r.def ILIKE '%regular_installment_cents%'
                            THEN
                                EXECUTE format('ALTER TABLE loans DROP CONSTRAINT IF EXISTS %I', r.conname);
                            END IF;
                        END LOOP;
                    END $$;
                """))
                conn.execute(text("""
                    CREATE OR REPLACE FUNCTION assert_loan_ledger_integrity(p_loan_id INTEGER)
                    RETURNS VOID LANGUAGE plpgsql AS $fn$
                    DECLARE
                        v_loan RECORD;
                        v_posted_amount BIGINT;
                        v_posted_interest BIGINT;
                        v_posted_principal BIGINT;
                        v_inst_amt_paid BIGINT;
                        v_inst_int_paid BIGINT;
                        v_inst_prin_paid BIGINT;
                        v_inst_rem_total BIGINT;
                    BEGIN
                        SELECT * INTO v_loan FROM loans WHERE id = p_loan_id;
                        IF NOT FOUND OR v_loan.status NOT IN ('ACTIVE', 'PAID_OFF') THEN
                            RETURN;
                        END IF;

                        SELECT
                            COALESCE(SUM(amount_cents), 0),
                            COALESCE(SUM(interest_applied_cents), 0),
                            COALESCE(SUM(principal_applied_cents), 0)
                        INTO v_posted_amount, v_posted_interest, v_posted_principal
                        FROM loan_payments
                        WHERE loan_id = p_loan_id AND status = 'POSTED';

                        SELECT
                            COALESCE(SUM(amount_paid_cents), 0),
                            COALESCE(SUM(interest_paid_cents), 0),
                            COALESCE(SUM(principal_paid_cents), 0),
                            COALESCE(SUM(remaining_amount_cents), 0)
                        INTO v_inst_amt_paid, v_inst_int_paid, v_inst_prin_paid, v_inst_rem_total
                        FROM loan_installments
                        WHERE loan_id = p_loan_id;

                        IF v_loan.amount_paid_cents != v_posted_amount OR v_loan.amount_paid_cents != v_inst_amt_paid THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: amount_paid_cents mismatch', p_loan_id;
                        END IF;

                        IF (v_loan.total_interest_cents - v_loan.remaining_contractual_interest_cents) != v_posted_interest
                           OR (v_loan.total_interest_cents - v_loan.remaining_contractual_interest_cents) != v_inst_int_paid THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: interest paid mismatch', p_loan_id;
                        END IF;

                        IF (v_loan.principal_cents - v_loan.unpaid_principal_cents) != v_posted_principal
                           OR (v_loan.principal_cents - v_loan.unpaid_principal_cents) != v_inst_prin_paid THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: principal paid mismatch', p_loan_id;
                        END IF;

                        IF v_loan.remaining_contract_total_cents != v_inst_rem_total THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: remaining_contract_total_cents mismatch', p_loan_id;
                        END IF;

                        IF (v_loan.unpaid_principal_cents + v_loan.remaining_contractual_interest_cents) != v_loan.remaining_contract_total_cents THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: unpaid principal + remaining interest != remaining total', p_loan_id;
                        END IF;

                        IF (v_loan.amount_paid_cents + v_loan.remaining_contract_total_cents) != v_loan.total_repayment_cents THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: amount_paid + remaining_total != total_repayment', p_loan_id;
                        END IF;
                    END;
                    $fn$;
                """))
    except Exception as e:
        logger.error(f"Schema compatibility guard failed: {e}")
        raise
