from pathlib import Path

from sqlalchemy import text

from app.db.database import engine


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def run_migrations(target_engine=None) -> None:
    """Apply each SQL migration once, in filename order.

    ``target_engine`` lets tests point at a throwaway database; by default the
    app's DATABASE_URL engine is used.
    """
    with (target_engine or engine).begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        applied = set(connection.execute(text("SELECT version FROM schema_migrations")).scalars())

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = path.name
            if version in applied:
                continue
            connection.exec_driver_sql(path.read_text(encoding="utf-8"))
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )