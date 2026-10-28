"""Normalisation and storage of browser CSP / Trusted Types violation reports (anonymous, aggregated)."""

import itertools
import json
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.csp_report import CspReport

MAX_BODY_BYTES = 8192
MAX_REPORTS = 10
HOURLY_UPSERT_CAP = 500

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG_NUM_RE = re.compile(r"\d{5,}")
_EMAIL_RE = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+")
_URL_RE = re.compile(r"(?:https?|wss?|blob|data):[^\s\"'<>]*", re.IGNORECASE)
_POLICY_RE = re.compile(r"named '([^']{1,64})'")
_SINK_RE = re.compile(r"^([A-Z]\w*(?: [a-z]\w*)?)(?:\||\s|$)")
_SAFE_NAME_RE = re.compile(r"[^\w.\-#]")
_KEYWORDS = {
    "inline",
    "eval",
    "trusted-types-policy",
    "trusted-types-policy-violation",
    "trusted-types-sink",
    "wasm-eval",
    "self",
}
_DIRECTIVES = {
    f"{base}{suffix}"
    for base, suffix in itertools.product(("script-src", "style-src"), ("", "-elem", "-attr"))
} | {
    "img-src", "connect-src", "font-src", "frame-src", "child-src", "worker-src", "object-src", "media-src",
    "manifest-src", "prefetch-src", "default-src", "base-uri", "form-action", "frame-ancestors", "navigate-to",
    "require-trusted-types-for", "trusted-types", "upgrade-insecure-requests", "sandbox", "report-to", "report-uri",
}  # fmt: skip
# Top-level app routes; anything else collapses so forged paths cannot mint unlimited rows.
_ROUTES = {
    "events", "clubs", "login", "register", "profile", "support", "chat", "chats", "privacy", "terms", "auth",
    "quizzes", "randomizer", "admin", "search", "books",
}  # fmt: skip
_SUBROUTES = {"new", "create", "edit", "settings", "join", ":id"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}
# Hosts the FE CSP legitimately allows; Supabase and BE hosts are added from settings.
_KNOWN_HOSTS = {
    "vercel.live", "va.vercel-scripts.com", "maps.googleapis.com", "maps.gstatic.com", "www.gstatic.com",
    "fonts.googleapis.com", "fonts.gstatic.com", "openlibrary.org", "covers.openlibrary.org", "books.google.com",
    "books.googleusercontent.com",
}  # fmt: skip

# Closed sets: sink and policy name come from attacker-controlled text, so only real ones keep their own row.
_SINKS = {
    "Element innerHTML", "Element outerHTML", "Element insertAdjacentHTML", "Element setAttribute", "Document write",
    "Document writeln", "HTMLScriptElement text", "HTMLScriptElement src", "HTMLScriptElement textContent",
    "HTMLScriptElement innerText", "HTMLIFrameElement srcdoc", "Function", "eval", "Worker", "SharedWorker",
    "ServiceWorkerContainer register", "DOMParser parseFromString", "Range createContextualFragment",
}  # fmt: skip
_POLICIES = {
    "default", "nextjs#bundler", "dompurify-internal", "@googlemaps/js-api-loader", "google-maps-api-loader",
    "google-maps-api#html", "lit-html", "ng-icons", "angular",
}  # fmt: skip
_SCHEMES = {"data", "blob", "filesystem", "about", "chrome-extension"}
_SCHEME_NAMES = {"data", "blob", "filesystem"}

_KEY = ("day", "env", "route", "directive", "disposition", "sink", "blocked", "policy_name")

# Process-level abuse cap: (hour bucket, upserts so far). Per instance, which is enough as a flood brake.
_budget: list[int] = [0, 0]


def reset_budget() -> None:
    _budget[:] = [0, 0]


def _take_budget(n: int) -> bool:
    hour = int(time.time() // 3600)
    if _budget[0] != hour:
        _budget[:] = [hour, 0]
    if _budget[1] + n > HOURLY_UPSERT_CAP:
        return False
    _budget[1] += n
    return True


def _first(body: dict[str, Any], *names: str) -> str:
    for name in names:
        value = body.get(name)
        if isinstance(value, str):
            return value
    return ""


def _classify_host(host: str, settings: Settings) -> str | None:
    """Return 'prod' | 'preview' | 'dev' for an accepted document host, else None."""
    allowed = {urlsplit(o).hostname for o in settings.ALLOWED_ORIGINS}
    if host in _LOCAL_HOSTS:
        return "dev" if settings.ENV != "production" and host in allowed else None
    if host in allowed:
        return "prod"
    if re.fullmatch(settings.CORS_ORIGIN_REGEX, f"https://{host}"):
        return "preview"
    return None


def _route(path: str) -> str:
    path = _LONG_NUM_RE.sub(":id", _UUID_RE.sub(":id", path))
    segments = [seg for seg in path.split("/") if seg]
    if not segments:
        return "/"
    if segments[0] not in _ROUTES:
        return "/other"
    kept = [segments[0]]
    if len(segments) > 1:
        kept.append(segments[1] if segments[1] in _SUBROUTES else ":x")
    if len(segments) > 2:
        kept.append("*")
    return "/" + "/".join(kept)


def _blocked(raw: str, doc_host: str, settings: Settings) -> str:
    raw = raw.strip()
    if raw in _KEYWORDS:
        return raw
    parts = urlsplit(raw)
    scheme = raw.split(":", 1)[0].lower()
    if scheme in _SCHEMES:
        return scheme if scheme in _SCHEME_NAMES else "other"
    if parts.scheme not in ("http", "https", "ws", "wss") or not parts.hostname:
        return ""
    host = parts.hostname
    if host == doc_host:
        return "self"
    known = _KNOWN_HOSTS | {urlsplit(u).hostname for u in (settings.SUPABASE_URL, settings.BACKEND_URL)}
    # Closed set: a forged host must not mint a new row; the first sample keeps the detail.
    return host if host in known else "other"


def _scrub_sample(sample: str) -> str:
    return _URL_RE.sub("[url]", _EMAIL_RE.sub("[email]", sample))[:120]


def normalise(raw: object, settings: Settings) -> dict[str, str] | None:
    """Map one raw report (Reporting API envelope or legacy body) to key fields, or None to drop it."""
    if not isinstance(raw, dict):
        return None
    envelope_url = raw.get("url") if isinstance(raw.get("url"), str) else ""
    body = raw.get("csp-report") if isinstance(raw.get("csp-report"), dict) else raw.get("body")
    if not isinstance(body, dict):
        return None
    doc = _first(body, "documentURL", "document-uri", "documentUri") or str(envelope_url)
    parts = urlsplit(doc)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    env = _classify_host(parts.hostname, settings)
    directive = _first(body, "effectiveDirective", "effective-directive", "violatedDirective", "violated-directive")
    if env is None or not directive:
        return None
    directive = directive.split(" ", 1)[0].lower()
    directive = directive if directive in _DIRECTIVES else "other"
    sample_raw = _first(body, "sample", "script-sample")
    policy = _POLICY_RE.search(sample_raw)
    sink = _SINK_RE.match(sample_raw) if directive == "require-trusted-types-for" else None
    sink_name = sink.group(1) if sink else ""
    policy_name = _SAFE_NAME_RE.sub("", policy.group(1)) if policy else ""
    disposition = _first(body, "disposition")
    return {
        "env": env,
        "route": _route(parts.path),
        "directive": directive,
        "disposition": "enforce" if disposition == "enforce" else "report",
        "sink": (sink_name if sink_name in _SINKS else "other") if sink_name else "",
        "blocked": _blocked(_first(body, "blockedURL", "blocked-uri", "blockedUri"), parts.hostname, settings),
        "policy_name": (policy_name if policy_name in _POLICIES else "other") if policy_name else "",
        "sample": _scrub_sample(sample_raw),
    }


def parse(payload: bytes, settings: Settings) -> list[dict[str, str]]:
    try:
        data = json.loads(payload)
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    rows = (normalise(item, settings) for item in items[:MAX_REPORTS])
    return [r for r in rows if r is not None]


async def store(db: AsyncSession, rows: list[dict[str, str]]) -> None:
    if not rows or not _take_budget(len(rows)):
        return
    insert = (sqlite if db.get_bind().dialect.name == "sqlite" else postgresql).insert
    day = datetime.now(UTC).date()
    table = CspReport.__table__
    for row in rows:
        stmt = insert(table).values(day=day, count=1, **row)
        await db.execute(stmt.on_conflict_do_update(index_elements=list(_KEY), set_={"count": table.c.count + 1}))
    await db.commit()
