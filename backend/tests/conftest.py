"""Shared pytest fixtures.

Tests run against a real Postgres database -- the `revrec_test` DB created by
scripts/init-test-db.sql inside the docker-compose container. Start it first:

    docker compose up -d

`conftest.py` is auto-discovered by pytest; fixtures here are available to
every test file without importing.
"""

import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.db import store

load_dotenv()

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://revrec:revrec@localhost:5432/revrec_test",
)

# short timeout so, when Postgres is down, DB tests fail in seconds instead of
# hanging on the OS connect timeout
_TEST_ENGINE = create_engine(
    TEST_DATABASE_URL, connect_args={"connect_timeout": 3}
)


@pytest.fixture(scope="session")
def _require_postgres():
    """Skip DB-backed tests with a clear message if Postgres isn't reachable.

    Not autouse -- the pure Pydantic-schema tests don't need a database.
    """
    try:
        with _TEST_ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(
            f"test database unreachable at {TEST_DATABASE_URL} "
            f"-- run `docker compose up -d`  ({exc.__class__.__name__})",
            allow_module_level=False,
        )


@pytest.fixture()
def test_database_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture()
def session(_require_postgres):
    """A fresh, empty store per test: drop + recreate every table, then a
    Session bound to the test database. Depends on _require_postgres, so a
    test that asks for `session` is skipped (not errored) when Postgres is down."""
    store.reset_db(TEST_DATABASE_URL)
    with store.get_session(TEST_DATABASE_URL) as sess:
        yield sess
