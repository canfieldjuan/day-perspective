from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from alembic.config import Config

SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _test_database_url() -> str:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.fail("TEST_DATABASE_URL is required for PostgreSQL/PostGIS integration tests.")
    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must target a disposable database ending in _test.")
    return database_url


@pytest.fixture(scope="session")
def migrated_database() -> Generator[str]:
    database_url = _test_database_url()
    parsed = make_url(database_url)
    quoted_name = str(parsed.database).replace('"', '""')
    admin_engine = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": parsed.database},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{quoted_name}"'))
        connection.execute(text(f'CREATE DATABASE "{quoted_name}"'))
    alembic_config = Config(str(SERVICE_ROOT / "alembic.ini"))
    alembic_config.attributes["database_url"] = database_url
    command.upgrade(alembic_config, "head")
    try:
        yield database_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": parsed.database},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{quoted_name}"'))
        admin_engine.dispose()


@pytest.fixture()
def session(migrated_database: str) -> Generator[Session]:
    engine = create_engine(migrated_database)
    with engine.begin() as connection:
        table_names = connection.execute(
            text(
                "SELECT quote_ident(tablename) FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'spatial_ref_sys' ORDER BY tablename"
            )
        ).scalars().all()
        connection.execute(text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE"))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as current_session:
        try:
            yield current_session
        finally:
            current_session.rollback()
    engine.dispose()
