WITH activity AS (
    SELECT
        count(*) FILTER (
            WHERE backend_type = 'client backend'
              AND pid <> pg_backend_pid()
        )::bigint AS connections_total,
        count(*) FILTER (
            WHERE backend_type = 'client backend'
              AND pid <> pg_backend_pid()
              AND state = 'active'
        )::bigint AS connections_active,
        count(*) FILTER (
            WHERE backend_type = 'client backend'
              AND pid <> pg_backend_pid()
              AND state = 'idle'
        )::bigint AS connections_idle
    FROM pg_stat_activity
), databases AS (
    SELECT count(*)::bigint AS connectable_databases
    FROM pg_database
    WHERE datallowconn
      AND NOT datistemplate
      AND has_database_privilege(current_user, datname, 'CONNECT')
), postmaster AS (
    SELECT pg_postmaster_start_time() AS postmaster_started_at
)
SELECT json_build_object(
    'version', current_setting('server_version'),
    'postmaster_started_at', postmaster.postmaster_started_at,
    'uptime_seconds', GREATEST(
        0,
        floor(extract(epoch FROM clock_timestamp() - postmaster.postmaster_started_at))
    )::bigint,
    'connections_total', activity.connections_total,
    'connections_active', activity.connections_active,
    'connections_idle', activity.connections_idle,
    'max_connections', current_setting('max_connections')::integer,
    'connectable_databases', databases.connectable_databases
)::text
FROM activity, databases, postmaster;
