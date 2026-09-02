-- odoo-instance-sdk PostgreSQL locks diagnostic, schema version 1.
-- __ODCLI_TOP__ is replaced only after integer bound validation.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE pgdiag_locks ON COMMIT DROP AS
WITH waiting AS (
    SELECT
        blocked.pid AS blocked_pid,
        pg_blocking_pids(blocked.pid) AS blocking_pids,
        NULLIF(blocked.application_name, '') AS application_name,
        NULLIF(blocked.usename, '') AS user_name,
        blocked.client_addr::text AS client_address,
        NULLIF(blocked.wait_event_type, '') AS wait_event_type,
        NULLIF(blocked.wait_event, '') AS wait_event,
        NULLIF(blocked.state, '') AS state,
        CASE WHEN blocked.xact_start IS NULL THEN NULL
             ELSE greatest(0.0, extract(epoch FROM clock_timestamp() - blocked.xact_start)::double precision)
        END AS transaction_age_seconds,
        CASE WHEN blocked.query_start IS NULL THEN NULL
             ELSE greatest(0.0, extract(epoch FROM clock_timestamp() - blocked.query_start)::double precision)
        END AS query_age_seconds,
        left(regexp_replace(coalesce(blocked.query, ''), '\s+', ' ', 'g'), 240) AS query_preview
    FROM pg_stat_activity AS blocked
    WHERE cardinality(pg_blocking_pids(blocked.pid)) > 0
      AND (blocked.wait_event IS NOT NULL OR blocked.wait_event_type IS NOT NULL)
)
SELECT * FROM waiting
ORDER BY query_age_seconds DESC NULLS LAST, blocked_pid ASC
LIMIT __ODCLI_TOP__;

SELECT json_build_object(
    'database', current_database(),
    'captured_at', clock_timestamp(),
    'rows', coalesce((SELECT json_agg(json_build_object(
        'blocked_pid', blocked_pid,
        'blocking_pids', blocking_pids,
        'application_name', application_name,
        'user_name', user_name,
        'client_address', client_address,
        'wait_event_type', wait_event_type,
        'wait_event', wait_event,
        'state', state,
        'transaction_age_seconds', transaction_age_seconds,
        'query_age_seconds', query_age_seconds,
        'query_preview', query_preview
    ) ORDER BY query_age_seconds DESC NULLS LAST, blocked_pid ASC)
    FROM pgdiag_locks), '[]'::json),
    'warnings', '[]'::json
);
COMMIT;
