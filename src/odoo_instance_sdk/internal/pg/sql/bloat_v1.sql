-- odoo-instance-sdk PostgreSQL bloat diagnostic, schema version 1.
-- __ODCLI_TOP__ and __ODCLI_EXACT_MAX_SCAN_BYTES__ are replaced only after bounds validation.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE pgdiag_bloat_capability (
    usable boolean NOT NULL DEFAULT false,
    warning_code text,
    table_ok boolean NOT NULL DEFAULT true,
    index_ok boolean NOT NULL DEFAULT true
) ON COMMIT DROP;
INSERT INTO pgdiag_bloat_capability VALUES (false, NULL, true, true);

CREATE TEMP TABLE pgdiag_bloat_tables (
    oid oid PRIMARY KEY, schema_name text NOT NULL, table_name text NOT NULL,
    total_bytes bigint NOT NULL, bloat_bytes bigint, bloat_ratio double precision,
    live_tuples bigint, dead_tuples bigint, last_vacuum_at timestamptz,
    last_autovacuum_at timestamptz, last_analyze_at timestamptz,
    last_autoanalyze_at timestamptz, method text NOT NULL
) ON COMMIT DROP;
CREATE TEMP TABLE pgdiag_bloat_indexes (
    oid oid PRIMARY KEY, schema_name text NOT NULL, index_name text NOT NULL,
    table_name text NOT NULL, access_method text NOT NULL, total_bytes bigint NOT NULL,
    bloat_bytes bigint, bloat_ratio double precision, scans bigint NOT NULL,
    unused_candidate boolean NOT NULL, method text NOT NULL
) ON COMMIT DROP;

INSERT INTO pgdiag_bloat_tables
SELECT c.oid, n.nspname, c.relname,
    greatest(0, pg_total_relation_size(c.oid))::bigint,
    CASE WHEN coalesce(s.n_live_tup, 0) + coalesce(s.n_dead_tup, 0) = 0 THEN NULL
         ELSE floor(pg_total_relation_size(c.oid) * least(1.0, greatest(0.0,
             s.n_dead_tup::double precision / NULLIF(s.n_live_tup + s.n_dead_tup, 0))))::bigint END,
    CASE WHEN coalesce(s.n_live_tup, 0) + coalesce(s.n_dead_tup, 0) = 0 THEN NULL
         ELSE least(1.0, greatest(0.0, s.n_dead_tup::double precision /
             NULLIF(s.n_live_tup + s.n_dead_tup, 0))) END,
    CASE WHEN s.n_live_tup IS NULL THEN NULL ELSE greatest(0, s.n_live_tup)::bigint END,
    CASE WHEN s.n_dead_tup IS NULL THEN NULL ELSE greatest(0, s.n_dead_tup)::bigint END,
    s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze,
    CASE WHEN coalesce(s.n_live_tup, 0) + coalesce(s.n_dead_tup, 0) = 0 THEN 'unavailable' ELSE 'estimate' END
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid
WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema');

INSERT INTO pgdiag_bloat_indexes
SELECT i.oid, n.nspname, i.relname, t.relname, am.amname,
    greatest(0, pg_relation_size(i.oid))::bigint,
    CASE WHEN bt.bloat_ratio IS NULL THEN NULL ELSE floor(pg_relation_size(i.oid) * bt.bloat_ratio)::bigint END,
    bt.bloat_ratio, greatest(0, coalesce(si.idx_scan, 0))::bigint,
    (NOT ix.indisprimary AND NOT ix.indisunique AND NOT ix.indisreplident AND coalesce(si.idx_scan, 0) = 0),
    CASE WHEN bt.bloat_ratio IS NULL THEN 'unavailable' ELSE 'estimate' END
FROM pg_class i JOIN pg_namespace n ON n.oid = i.relnamespace
JOIN pg_index ix ON ix.indexrelid = i.oid JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_am am ON am.oid = i.relam JOIN pgdiag_bloat_tables bt ON bt.oid = t.oid
LEFT JOIN pg_stat_user_indexes si ON si.indexrelid = i.oid
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema');

-- Only the already-selected, size-bounded candidates enter optional exact calls.
DO $$
DECLARE item record; exact_result record; relation_name text;
BEGIN
    FOR item IN SELECT * FROM (
        SELECT * FROM pgdiag_bloat_tables
        ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, table_name ASC
        LIMIT __ODCLI_TOP__
    ) AS selected_tables
        WHERE total_bytes <= __ODCLI_EXACT_MAX_SCAN_BYTES__
        ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, table_name ASC
    LOOP
        BEGIN
            relation_name := format('%I.%I', item.schema_name, item.table_name);
            EXECUTE 'SELECT table_len, dead_tuple_len, free_space, tuple_count, dead_tuple_count FROM pgstattuple($1::regclass)'
                INTO exact_result USING relation_name;
            UPDATE pgdiag_bloat_tables SET
                bloat_bytes = greatest(0, exact_result.dead_tuple_len + exact_result.free_space)::bigint,
                bloat_ratio = CASE WHEN exact_result.table_len = 0 THEN 0.0 ELSE least(1.0, greatest(0.0,
                    (exact_result.dead_tuple_len + exact_result.free_space)::double precision / exact_result.table_len)) END,
                live_tuples = greatest(0, exact_result.tuple_count)::bigint,
                dead_tuples = greatest(0, exact_result.dead_tuple_count)::bigint,
                method = 'exact' WHERE oid = item.oid;
        EXCEPTION
            WHEN undefined_table OR undefined_function THEN
                UPDATE pgdiag_bloat_capability SET table_ok = false, warning_code = coalesce(warning_code, 'pgstattuple_not_installed');
            WHEN insufficient_privilege THEN
                UPDATE pgdiag_bloat_capability SET table_ok = false, warning_code = coalesce(warning_code, 'pgstattuple_privilege_denied');
            WHEN OTHERS THEN
                UPDATE pgdiag_bloat_capability SET table_ok = false, warning_code = coalesce(warning_code, 'pgstattuple_query_failed');
        END;
    END LOOP;

    FOR item IN SELECT * FROM (
        SELECT * FROM pgdiag_bloat_indexes
        WHERE access_method = 'btree'
        ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, index_name ASC
        LIMIT __ODCLI_TOP__
    ) AS selected_indexes
        WHERE total_bytes <= __ODCLI_EXACT_MAX_SCAN_BYTES__
        ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, index_name ASC
    LOOP
        BEGIN
            relation_name := format('%I.%I', item.schema_name, item.index_name);
            EXECUTE 'SELECT leaf_fragmentation FROM pgstatindex($1::regclass)'
                INTO exact_result USING relation_name;
            UPDATE pgdiag_bloat_indexes SET
                bloat_bytes = floor(total_bytes * least(1.0, greatest(0.0, exact_result.leaf_fragmentation / 100.0)))::bigint,
                bloat_ratio = least(1.0, greatest(0.0, exact_result.leaf_fragmentation / 100.0)),
                method = 'exact' WHERE oid = item.oid;
        EXCEPTION
            WHEN undefined_table OR undefined_function THEN
                UPDATE pgdiag_bloat_capability SET index_ok = false, warning_code = coalesce(warning_code, 'pgstattuple_not_installed');
            WHEN insufficient_privilege THEN
                UPDATE pgdiag_bloat_capability SET index_ok = false, warning_code = coalesce(warning_code, 'pgstattuple_privilege_denied');
            WHEN OTHERS THEN
                UPDATE pgdiag_bloat_capability SET index_ok = false, warning_code = coalesce(warning_code, 'pgstattuple_query_failed');
        END;
    END LOOP;
    UPDATE pgdiag_bloat_capability SET usable = table_ok AND index_ok;
END;
$$;

SELECT json_build_object(
    'database', current_database(), 'captured_at', clock_timestamp(),
    'tables', coalesce((SELECT json_agg(json_build_object(
        'schema', schema_name, 'table', table_name, 'total_bytes', total_bytes,
        'bloat_bytes', bloat_bytes, 'bloat_ratio', bloat_ratio, 'live_tuples', live_tuples,
        'dead_tuples', dead_tuples, 'last_vacuum_at', last_vacuum_at,
        'last_autovacuum_at', last_autovacuum_at, 'last_analyze_at', last_analyze_at,
        'last_autoanalyze_at', last_autoanalyze_at, 'method', method
    ) ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, table_name ASC)
    FROM (SELECT * FROM pgdiag_bloat_tables ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, table_name ASC LIMIT __ODCLI_TOP__) AS x), '[]'::json),
    'indexes', coalesce((SELECT json_agg(json_build_object(
        'schema', schema_name, 'index', index_name, 'table', table_name, 'total_bytes', total_bytes,
        'bloat_bytes', bloat_bytes, 'bloat_ratio', bloat_ratio, 'scans', scans,
        'unused_candidate', unused_candidate, 'method', method
    ) ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, index_name ASC)
    FROM (SELECT * FROM pgdiag_bloat_indexes ORDER BY bloat_bytes DESC NULLS LAST, total_bytes DESC, schema_name ASC, index_name ASC LIMIT __ODCLI_TOP__) AS x), '[]'::json),
    'capabilities', json_build_object('pgstattuple', (SELECT usable FROM pgdiag_bloat_capability)),
    'warnings', (
        SELECT coalesce(json_agg(json_build_object('code', code, 'message', message) ORDER BY ordinal), '[]'::json)
        FROM (
            SELECT 1 AS ordinal, warning_code AS code,
                CASE warning_code
                    WHEN 'pgstattuple_not_installed' THEN 'pgstattuple is not installed; exact bloat fields retain estimates'
                    WHEN 'pgstattuple_privilege_denied' THEN 'pgstattuple access was denied; exact bloat fields retain estimates'
                    WHEN 'pgstattuple_query_failed' THEN 'pgstattuple query failed; exact bloat fields retain estimates'
                END AS message
            FROM pgdiag_bloat_capability WHERE warning_code IS NOT NULL
            UNION ALL SELECT 2, 'cumulative_statistics', 'scan counters and unused candidates are cumulative PostgreSQL statistics'
        ) AS warnings
    )
);
COMMIT;
