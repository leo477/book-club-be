import pytest

from tests._pg_safety import check_pg_test_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://test:test@localhost:5432/bookclub_pgtest",
        "postgresql+asyncpg://u:p@127.0.0.1/test_db",
        "postgresql+asyncpg://u:p@LOCALHOST:5432/test",
        "postgresql+asyncpg://u:p@postgres-test/test",
    ],
)
def test_allows_local_test_databases(url):
    assert check_pg_test_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@db.abc.supabase.co:5432/test",
        "postgresql+asyncpg://u:p@aws-0-eu.pooler.supabase.com:6543/test",
        "postgresql+asyncpg://u:p@localhost:5432/bookclub",
        "postgresql+asyncpg://u:p@prod.example.com/test",
        "not a url",
        "postgresql+asyncpg://u:p@localhost/x_test?host=evil.example.com",
        "postgresql+asyncpg://u:p@localhost/test?host=a.supabase.co",
        "postgresql+asyncpg://u:p@localhost/test?host=/var/run/postgresql",
        "postgresql+asyncpg://u:p@localhost/test?port=6543",
        "postgresql+asyncpg://u:p@[::1]/test",
        "postgresql+asyncpg://localhost@evil.supabase.co/test",
        "postgresql+asyncpg://u:p@localhost,evil.example.com/test",
        "postgresql+asyncpg://u:p@localhost/prod_test",
        "postgresql+asyncpg://u:p@localhost/latest",
    ],
)
def test_rejects_non_test_databases(url):
    assert check_pg_test_url(url) is not None
