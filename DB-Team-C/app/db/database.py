from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL is None:
    raise RuntimeError("DATABASE_URL is not set in the environment")

# Postgres is hosted (Supabase) and reached through their connection pooler.
#
# Two things bite here:
#
#   1. The pooler drops idle connections, so without pre_ping the first request
#      after an idle spell gets a dead connection from the pool.
#   2. The pooler hostname resolves to several IPs and not all of them are
#      reachable from every network. libpq walks the list, so a short
#      connect_timeout is what makes it give up on a black-holed address and
#      fail over to a working one. The default (no timeout) hangs long enough
#      for the gateway to return 502.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"connect_timeout": 3},
)


def warm_up_pool() -> bool:
    """
    Open one connection at startup so a patient request is never the thing that
    pays the cold-connect cost. Best effort: a failure here is logged and the
    service still starts, since the DB may simply be slow to reach.
    """
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - startup must not crash on this
        print(f"[db] Pool warm-up failed, continuing anyway: {exc}")
        return False
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()
