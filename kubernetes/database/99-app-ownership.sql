-- Kubernetes only (CloudNativePG). Runs after 01-schema.sql and 02-seed.sql.
--
-- Docker Compose runs the init scripts as locker_user, so that user owns every
-- table. CloudNativePG runs them as the postgres superuser instead, so hand the
-- objects over to the application user, otherwise the backend gets
-- "permission denied" on its own tables.
DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN SELECT format('%I.%I', schemaname, tablename) AS name
               FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE 'ALTER TABLE ' || obj.name || ' OWNER TO locker_user';
    END LOOP;

    -- Sequences owned by a column follow their table; change the rest here.
    FOR obj IN SELECT c.oid::regclass::text AS name
               FROM pg_class c
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relkind = 'S' AND n.nspname = 'public'
                 AND NOT EXISTS (SELECT 1 FROM pg_depend d
                                 WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')) LOOP
        EXECUTE 'ALTER SEQUENCE ' || obj.name || ' OWNER TO locker_user';
    END LOOP;

    FOR obj IN SELECT format('%I.%I', schemaname, viewname) AS name
               FROM pg_views WHERE schemaname = 'public' LOOP
        EXECUTE 'ALTER VIEW ' || obj.name || ' OWNER TO locker_user';
    END LOOP;
END $$;
