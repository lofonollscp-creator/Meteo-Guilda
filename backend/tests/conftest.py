import os

import pytest

os.environ.setdefault("MG_DATABASE_URL", "postgresql://mg:mg@127.0.0.1:5432/meteoguilda_test")

from meteoguilda import db  # noqa: E402


@pytest.fixture(scope="session")
def pg():
    if not db.available():
        pytest.skip("PostgreSQL/PostGIS no disponible (MG_DATABASE_URL)")
    db.init_schema()
    yield
    db.close()


@pytest.fixture
def clean(pg):
    with db.pool().connection() as con:
        con.execute("TRUNCATE obs, repeaters RESTART IDENTITY")
    yield
