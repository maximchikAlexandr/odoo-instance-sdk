-- odoo-instance-sdk PostgreSQL statistics diagnostic, schema version 1.
-- __ODCLI_TOP__ is replaced only after integer bound validation.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE pgdiag_stats_capability (
    usable boolean NOT NULL DEFAULT false,
    warning_code text
) ON COMMIT DROP;
INSERT INTO pgdiag_stats_capability VALUES (false, NULL);

CREATE TEMP TABLE pgdiag_stats_tables (
    oid oid PRIMARY KEY,
    schema_name text NOT NULL,
    table_name text NOT NULL,
    estimated_live_rows bigint NOT NULL,
    heap_bytes bigint NOT NULL,
    toast_bytes bigint NOT NULL,
    index_bytes bigint NOT NULL,
    total_bytes bigint NOT NULL,
    index_count bigint NOT NULL,
    heap_blocks_read bigint NOT NULL,
    heap_blocks_hit bigint NOT NULL,
    index_blocks_read bigint NOT NULL,
    index_blocks_hit bigint NOT NULL,
    shared_buffer_bytes bigint,
    shared_buffer_ratio double precision,
    hot_page_ratio double precision
) ON COMMIT DROP;

CREATE TEMP TABLE pgdiag_stats_indexes (
    schema_name text NOT NULL,
    index_name text NOT NULL,
    table_name text NOT NULL,
    access_method text NOT NULL,
    columns text[] NOT NULL,
    bytes bigint NOT NULL,
    scans bigint NOT NULL
) ON COMMIT DROP;

INSERT INTO pgdiag_stats_tables (
    oid, schema_name, table_name, estimated_live_rows, heap_bytes, toast_bytes,
    index_bytes, total_bytes, index_count, heap_blocks_read, heap_blocks_hit,
    index_blocks_read, index_blocks_hit
)
SELECT
    c.oid,
    n.nspname,
    c.relname,
    greatest(0, coalesce(s.n_live_tup, 0))::bigint,
    greatest(0, pg_relation_size(c.oid, 'main'))::bigint,
    greatest(0, CASE WHEN c.reltoastrelid = 0 THEN 0 ELSE pg_total_relation_size(c.reltoastrelid) END)::bigint,
    greatest(0, pg_indexes_size(c.oid))::bigint,
    greatest(0, pg_total_relation_size(c.oid))::bigint,
    greatest(0, (SELECT count(*) FROM pg_index ix WHERE ix.indrelid = c.oid))::bigint,
    greatest(0, coalesce(s.heap_blks_read, 0))::bigint,
    greatest(0, coalesce(s.heap_blks_hit, 0))::bigint,
    greatest(0, coalesce(s.idx_blks_read, 0))::bigint,
    greatest(0, coalesce(s.idx_blks_hit, 0))::bigint
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables AS s ON s.relid = c.oid
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY total_bytes DESC, schema_name ASC, table_name ASC;

INSERT INTO pgdiag_stats_indexes (schema_name, index_name, table_name, access_method, columns, bytes, scans)
SELECT
    n.nspname,
    i.relname,
    t.relname,
    am.amname,
    coalesce(array_agg(a.attname ORDER BY keys.ordinality) FILTER (WHERE a.attname IS NOT NULL), ARRAY[]::text[]),
    greatest(0, pg_relation_size(i.oid))::bigint,
    greatest(0, coalesce(si.idx_scan, 0))::bigint
FROM pg_class AS i
JOIN pg_namespace AS n ON n.oid = i.relnamespace
JOIN pg_index AS ix ON ix.indexrelid = i.oid
JOIN pg_class AS t ON t.oid = ix.indrelid
JOIN pg_am AS am ON am.oid = i.relam
LEFT JOIN pg_stat_user_indexes AS si ON si.indexrelid = i.oid
LEFT JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS keys(attnum, ordinality) ON true
LEFT JOIN pg_attribute AS a ON a.attrelid = t.oid AND a.attnum = keys.attnum
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
GROUP BY n.nspname, i.relname, t.relname, am.amname, i.oid, si.idx_scan
ORDER BY bytes DESC, schema_name ASC, index_name ASC;

-- Optional cache data is the only dynamic catalog boundary.  All state is
-- session-local and all non-classified errors are reported without leaking text.
DO $$
BEGIN
    BEGIN
        EXECUTE $sql$
            UPDATE pgdiag_stats_tables AS t
            SET shared_buffer_bytes = q.cached_buffers * current_setting('block_size')::int,
                shared_buffer_ratio = CASE WHEN t.total_bytes = 0 THEN 0.0
                    ELSE least(1.0, (q.cached_buffers * current_setting('block_size')::numeric) / t.total_bytes) END,
                hot_page_ratio = CASE WHEN q.cached_buffers = 0 THEN 0.0
                    ELSE q.hot_buffers::numeric / q.cached_buffers END
            FROM (
                SELECT c.oid,
                       count(*) FILTER (WHERE b.forknum = 0) AS cached_buffers,
                       count(*) FILTER (WHERE b.forknum = 0 AND b.usagecount >= 3) AS hot_buffers
                FROM pg_buffercache AS b
                JOIN pg_class AS c ON c.relfilenode = b.relfilenode
                    AND (b.reltablespace = 0 OR b.reltablespace = c.reltablespace)
                    AND (b.reldatabase = 0 OR b.reldatabase = (SELECT oid FROM pg_database WHERE datname = current_database()))
                GROUP BY c.oid
            ) AS q
            WHERE q.oid = t.oid
        $sql$;
        UPDATE pgdiag_stats_capability SET usable = true, warning_code = NULL;
    EXCEPTION
        WHEN undefined_table OR undefined_function THEN
            UPDATE pgdiag_stats_capability SET usable = false, warning_code = 'pg_buffercache_not_installed';
        WHEN insufficient_privilege THEN
            UPDATE pgdiag_stats_capability SET usable = false, warning_code = 'pg_buffercache_privilege_denied';
        WHEN OTHERS THEN
            UPDATE pgdiag_stats_capability SET usable = false, warning_code = 'pg_buffercache_query_failed';
    END;
END;
$$;

SELECT json_build_object(
    'summary', json_build_object(
        'database', current_database(),
        'server_version', current_setting('server_version'),
        'captured_at', clock_timestamp(),
        'stats_since', (SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()),
        'database_bytes', greatest(0, pg_database_size(current_database()))::bigint,
        'block_size_bytes', greatest(0, current_setting('block_size')::int)::bigint
    ),
    'tables', coalesce((SELECT json_agg(json_build_object(
        'schema', schema_name, 'table', table_name, 'estimated_live_rows', estimated_live_rows,
        'heap_bytes', heap_bytes, 'toast_bytes', toast_bytes, 'index_bytes', index_bytes,
        'total_bytes', total_bytes, 'index_count', index_count,
        'heap_blocks_read', heap_blocks_read, 'heap_blocks_hit', heap_blocks_hit,
        'index_blocks_read', index_blocks_read, 'index_blocks_hit', index_blocks_hit,
        'shared_buffer_bytes', shared_buffer_bytes, 'shared_buffer_ratio', shared_buffer_ratio,
        'hot_page_ratio', hot_page_ratio
    ) ORDER BY total_bytes DESC, schema_name ASC, table_name ASC) FROM (SELECT * FROM pgdiag_stats_tables
        ORDER BY total_bytes DESC, schema_name ASC, table_name ASC LIMIT __ODCLI_TOP__) AS x), '[]'::json),
    'indexes', coalesce((SELECT json_agg(json_build_object(
        'schema', schema_name, 'index', index_name, 'table', table_name,
        'access_method', access_method, 'columns', columns, 'bytes', bytes, 'scans', scans
    ) ORDER BY bytes DESC, schema_name ASC, index_name ASC) FROM (SELECT * FROM pgdiag_stats_indexes
        ORDER BY bytes DESC, schema_name ASC, index_name ASC LIMIT __ODCLI_TOP__) AS x), '[]'::json),
    'capabilities', json_build_object('pg_buffercache', (SELECT usable FROM pgdiag_stats_capability)),
    'warnings', (
        SELECT coalesce(json_agg(json_build_object('code', code, 'message', message) ORDER BY ordinal), '[]'::json)
        FROM (
            SELECT 1 AS ordinal, warning_code AS code,
                CASE warning_code
                    WHEN 'pg_buffercache_not_installed' THEN 'pg_buffercache is not installed; cache fields are unavailable'
                    WHEN 'pg_buffercache_privilege_denied' THEN 'pg_buffercache access was denied; cache fields are unavailable'
                    WHEN 'pg_buffercache_query_failed' THEN 'pg_buffercache query failed; cache fields are unavailable'
                END AS message
            FROM pgdiag_stats_capability WHERE warning_code IS NOT NULL
            UNION ALL SELECT 2, 'cumulative_statistics', 'read, hit, and scan values are cumulative PostgreSQL counters'
        ) AS warnings
    )
);
COMMIT;
