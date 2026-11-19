import os
import time

from psycopg2.pool import ThreadedConnectionPool

_pool = None


def _dsn():
    return dict(
        host=os.getenv("DB_HOST", "postgres"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
        dbname=os.getenv("DB_NAME", "postgres"),
    )


def init(retries=15):
    """Wait for Postgres, create the table, set up the pool."""
    global _pool
    for i in range(1, retries + 1):
        try:
            _pool = ThreadedConnectionPool(1, 10, **_dsn())
            with query() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notifications (
                        id         BIGSERIAL PRIMARY KEY,
                        user_id    TEXT NOT NULL,
                        title      TEXT NOT NULL,
                        message    TEXT NOT NULL DEFAULT '',
                        type       TEXT NOT NULL DEFAULT 'INFO',
                        is_read    BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_notifications_user
                        ON notifications (user_id, created_at DESC);
                    """
                )
            print("[db] connected, schema ready", flush=True)
            return
        except Exception as e:  # noqa: BLE001
            print(f"[db] not ready ({i}/{retries}): {e}", flush=True)
            time.sleep(2)
    raise RuntimeError("Could not connect to Postgres")


class query:
    """Context manager: yields a dict-style cursor, commits on success."""

    def __enter__(self):
        from psycopg2.extras import RealDictCursor

        self.conn = _pool.getconn()
        self.cur = self.conn.cursor(cursor_factory=RealDictCursor)
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
            self.cur.close()
        finally:
            _pool.putconn(self.conn)
        return False
