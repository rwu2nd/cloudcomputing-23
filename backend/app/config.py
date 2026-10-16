"""Runtime configuration, read from environment variables.

The same variable names are used by docker-compose.yml and by the Kubernetes
ConfigMap / Secret, so this module works unchanged in all three places.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote_plus


@dataclass(frozen=True)
class Settings:
    app_env: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str

    @property
    def database_url(self) -> str:
        """Connection URL built from the POSTGRES_* variables.

        Handy for SQLAlchemy / psycopg / asyncpg once the database layer is
        implemented. The password is URL-encoded so special characters are safe.
        """
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    # No default credentials on purpose: they must come from the environment.
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", ""),
        postgres_user=os.getenv("POSTGRES_USER", ""),
        postgres_password=os.getenv("POSTGRES_PASSWORD", ""),
    )
