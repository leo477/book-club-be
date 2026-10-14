"""Safety guard for the Postgres tests: they run migrations and TRUNCATE, so only allow throwaway DBs."""

import re

from sqlalchemy.engine import make_url

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres", "postgres-test"}
FORBIDDEN_HOST_PARTS = ("supabase.co", "pooler")
# e.g. test, test_db, bookclub_pgtest; not prod_test, latest, contest
DB_NAME_RE = re.compile(r"(bookclub_)?(pg)?test(_[a-z0-9_]+)?")


def check_pg_test_url(url: str) -> str | None:
    """Return a reason the URL is unsafe for destructive tests, or None when it is fine."""
    try:
        parsed = make_url(url)
    except Exception as exc:
        return f"unparseable URL ({exc})"
    if parsed.query:
        # the asyncpg dialect merges the query into connect kwargs (?host=, ?port=, ?dsn=, ...)
        return f"query parameters are not allowed: {dict(parsed.query)}"
    host = (parsed.host or "").lower()
    if any(part in host for part in FORBIDDEN_HOST_PARTS):
        return f"host {host!r} looks like a hosted/pooled database"
    if host not in ALLOWED_HOSTS:
        return f"host {host!r} is not one of {sorted(ALLOWED_HOSTS)}"
    if not DB_NAME_RE.fullmatch((parsed.database or "").lower()):
        return f"database name {parsed.database!r} must look like test, test_*, bookclub_pgtest"
    return None
