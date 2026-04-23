from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker


# -----------------------------------------------------------------------------
# Environment loading (LOCAL-ONLY)
# - In production (e.g. Render), do NOT depend on backend/.env.
# - Locally, load backend/.env if present.
# -----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def _is_truthy_env(name: str) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _should_load_dotenv() -> bool:
    """
    Loads backend/.env only for local/dev usage.

    Production should rely on real environment variables (Render dashboard).
    Detection rules:
      - If RENDER is set/truthy OR ENV/ENVIRONMENT indicates production -> don't load .env
      - Otherwise, if backend/.env exists -> load .env
    """
    if _is_truthy_env("RENDER"):
        return False

    env = (os.getenv("ENV", "") or "").strip().lower()
    environment = (os.getenv("ENVIRONMENT", "") or "").strip().lower()

    if env in {"prod", "production"} or environment in {"prod", "production"}:
        return False

    return ENV_PATH.exists()


if _should_load_dotenv():
    # override=False ensures real environment variables always win
    load_dotenv(dotenv_path=ENV_PATH, override=False)


Base = declarative_base()

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _default_sqlite_url() -> str:
    """
    Safe local default when DATABASE_URL is not provided.
    Stores DB file in /backend/app.db
    """
    db_path = (BASE_DIR / "app.db").resolve()
    return f"sqlite:///{db_path.as_posix()}"


def _force_psycopg3_driver(db_url: str) -> str:
    """
    Ensure SQLAlchemy uses psycopg v3 (psycopg) instead of defaulting to psycopg2.

    - postgresql://...  -> postgresql+psycopg://...
    - postgres://...    -> postgresql+psycopg://...
    - If already has an explicit driver (postgresql+something://), leave it as-is.
    """
    u = (db_url or "").strip()
    if not u:
        return u

    # Normalize legacy scheme first
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]

    # If driver already specified, do nothing (e.g., postgresql+psycopg:// or postgresql+psycopg2://)
    if u.startswith("postgresql+"):
        return u

    # Force psycopg v3 for standard postgresql:// URLs
    if u.startswith("postgresql://"):
        return "postgresql+psycopg://" + u[len("postgresql://") :]

    return u


def get_database_url() -> str:
    """
    Returns effective DB URL.

    Priority:
      1) Environment DATABASE_URL
      2) backend/app.db SQLite fallback (local/dev friendly)

    Normalizes:
      - postgres://  -> postgresql://
    Forces:
      - postgresql:// -> postgresql+psycopg://  (so Render works with psycopg v3)
    """
    db_url = (os.getenv("DATABASE_URL") or "").strip()

    if not db_url:
        return _default_sqlite_url()

    db_url = _force_psycopg3_driver(db_url)
    return db_url


def init_engine() -> tuple[Engine, sessionmaker]:
    global _engine, _SessionLocal

    if _engine is not None and _SessionLocal is not None:
        return _engine, _SessionLocal

    db_url = get_database_url()

    connect_args = {}
    if db_url.startswith("sqlite:///"):
        # Needed for SQLite with FastAPI + threads
        connect_args = {"check_same_thread": False}

    _engine = create_engine(
        db_url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )

    _SessionLocal = sessionmaker(
        bind=_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    return _engine, _SessionLocal


def get_session():
    _, SessionLocal = init_engine()
    return SessionLocal()