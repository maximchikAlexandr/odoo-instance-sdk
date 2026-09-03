-- odoo-instance-sdk monitoring extension initialization, schema version 1.
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE pgdiag_monitoring_outcomes (
    extension_name text PRIMARY KEY,
    outcome text NOT NULL
) ON COMMIT DROP;
INSERT INTO pgdiag_monitoring_outcomes (extension_name, outcome)
VALUES ('pg_buffercache', 'pending'), ('pgstattuple', 'pending');

DO $$
DECLARE
    item record;
    is_available boolean;
BEGIN
    FOR item IN
        SELECT extension_name
        FROM pgdiag_monitoring_outcomes
        ORDER BY extension_name ASC
    LOOP
        IF EXISTS (
            SELECT 1
            FROM pg_extension
            WHERE extname = item.extension_name
        ) THEN
            UPDATE pgdiag_monitoring_outcomes
            SET outcome = 'already_present'
            WHERE extension_name = item.extension_name;
        ELSE
            SELECT EXISTS (
                SELECT 1
                FROM pg_available_extensions
                WHERE name = item.extension_name
            )
            INTO is_available;
            IF NOT is_available THEN
                UPDATE pgdiag_monitoring_outcomes
                SET outcome = 'not_available'
                WHERE extension_name = item.extension_name;
            ELSE
                BEGIN
                    EXECUTE format('CREATE EXTENSION %I', item.extension_name);
                    UPDATE pgdiag_monitoring_outcomes
                    SET outcome = 'installed'
                    WHERE extension_name = item.extension_name;
                EXCEPTION
                    WHEN insufficient_privilege THEN
                        UPDATE pgdiag_monitoring_outcomes
                        SET outcome = 'privilege_denied'
                        WHERE extension_name = item.extension_name;
                END;
            END IF;
        END IF;
    END LOOP;
END;
$$;

SELECT json_build_object(
    'installed', coalesce((
        SELECT json_agg(extension_name ORDER BY extension_name ASC)
        FROM pgdiag_monitoring_outcomes
        WHERE outcome = 'installed'
    ), '[]'::json),
    'already_present', coalesce((
        SELECT json_agg(extension_name ORDER BY extension_name ASC)
        FROM pgdiag_monitoring_outcomes
        WHERE outcome = 'already_present'
    ), '[]'::json),
    'skipped', coalesce((
        SELECT json_agg(json_build_object(
            'extension', extension_name,
            'reason', outcome
        ) ORDER BY extension_name ASC)
        FROM pgdiag_monitoring_outcomes
        WHERE outcome IN ('not_available', 'privilege_denied')
    ), '[]'::json)
);
COMMIT;
