"""SCRUM-166: X-Forwarded-For must not be spoofable by the client."""

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


async def _run(trusted: str, peer: str, xff: str) -> tuple[str, int] | None:
    seen: dict = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"]

    scope = {
        "type": "http",
        "client": (peer, 5555),
        "headers": [(b"x-forwarded-for", xff.encode())],
    }
    await ProxyHeadersMiddleware(app, trusted_hosts=trusted)(scope, None, None)
    return seen["client"]


async def test_spoofed_leftmost_ignored_behind_trusted_proxy():
    # Render appends the real client (198.51.100.9) after the spoofed value.
    client = await _run("10.0.0.0/8", "10.216.24.138", "203.0.113.7, 198.51.100.9")
    assert client[0] == "198.51.100.9"


async def test_wildcard_is_spoofable_regression_guard():
    client = await _run("*", "10.216.24.138", "203.0.113.7, 198.51.100.9")
    assert client[0] == "203.0.113.7"


async def test_header_ignored_from_untrusted_peer():
    client = await _run("10.0.0.0/8", "198.51.100.9", "203.0.113.7")
    assert client[0] == "198.51.100.9"


def test_dockerfile_does_not_trust_all():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "Dockerfile").read_text()
    assert "forwarded-allow-ips=*" not in text
    assert 'FORWARDED_ALLOW_IPS="10.0.0.0/8"' in text
