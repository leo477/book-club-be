# WebSocket Security Analysis

**Date:** 2026-05-27  
**Scope:** `app/routers/chat.py` — `ConnectionManager` + `websocket_endpoint`

---

## Current Architecture

| Aspect | Implementation | Assessment |
|--------|---------------|------------|
| Auth | JWT via `?token=` query param | ✅ Standard practice for WS — browsers cannot send custom headers during the WS handshake |
| Token validation | `decode_access_token()` — supports Supabase JWKS + HS256 | ✅ Correct |
| Membership check | `ClubMember` row checked on connect | ✅ Correct |
| Stale snapshot fix | `await db.rollback()` before reads | ✅ Fixes WS-403-after-join |
| Ban enforcement | Checked on every message with 30s in-memory TTL cache | ✅ Good |
| Broadcast | Single-process in-memory `ConnectionManager` | ✅ Appropriate for current Render single-instance deployment |
| Connection cleanup | `finally` block calls `disconnect()` | ✅ Correct |

---

## Risk Areas

### 1. JWT in URL query parameter ⚠️ Low–Medium

**Risk:** The access token appears in server access logs (`GET /chat/rooms/{id}?token=<jwt>`).

**Current mitigations:**
- The token TTL is controlled by Supabase (typically 1 hour).
- The endpoint is `wss://` — the query string is encrypted in transit.
- Token is not stored in browser history for WS connections.

**Recommended actions (non-urgent):**
1. Configure Render log scrubbing or Uvicorn access-log filter to redact `?token=.*`.
2. For a future sprint: issue short-lived (60s) one-time WS tokens via a new endpoint `POST /api/v1/chat/ws-token`. This limits the exposure window dramatically.

### 2. In-memory `ConnectionManager` ✅ Safe at current scale

**Context:** The `ConnectionManager` uses a module-level `defaultdict`. This is:
- ✅ Correct for single-process deployment (Render free/starter tier = 1 instance)
- ❌ **Will break** with horizontal scaling (multiple instances) — connections on instance A cannot receive broadcasts from instance B

**When to add Redis pub/sub:**  
Only when horizontal scaling is required. The project already has Redis infrastructure in place (`REDIS_URL`, `get_redis()` dependency, pool in `lifespan`). Migration path when needed:
1. Each instance subscribes to `redis:chat:room:{room_id}` channels.
2. On new message: publish to Redis instead of broadcasting directly.
3. Each instance rebroadcasts received Redis messages to its local WS connections.
4. Estimated implementation time: 1–2 days.

**Verdict: Redis is NOT needed now.** Adding it prematurely introduces operational complexity (connection failures, latency, one more service to monitor) with zero benefit on a single-instance deployment.

### 3. Ban cache (30s TTL) ✅ Acceptable

The per-request in-memory ban cache reduces DB load. A 30-second window where a newly banned user can still send messages is acceptable for a book club chat context.

---

## Summary

**Overall verdict: Architecture is safe for current scale.**

The only actionable item of medium priority is log scrubbing to prevent JWT token exposure in server logs. All other items are either correctly implemented or deferred to scaling phase.

| Priority | Action |
|----------|--------|
| Medium | Scrub `?token=` from Render/Uvicorn access logs |
| Low | Short-lived WS tokens (future sprint) |
| Future | Redis pub/sub (only when multi-instance scaling is required) |
