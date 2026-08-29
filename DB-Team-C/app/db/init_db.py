"""Apply the PostgreSQL migrations.

Run after creating the database in Postgres:

    python -m app.db.init_db
"""

from app.db.migrations import run_migrations


def init_db():
    run_migrations()


if __name__ == "__main__":
    init_db()
    print("Applied database migrations using DATABASE_URL from .env")
