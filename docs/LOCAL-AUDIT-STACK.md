# Local audit stack (SCRUM-132)

Disposable backend for the frontend's `npm run audit:full`. Everything is local; no real Supabase/Render/production credentials are used.

## Services

| Service | Image | Notes |
| --- | --- | --- |
| db | postgres:16 | TLS on with a self-signed cert (the app hardcodes `ssl=require`); databases `bookclub` and `gotrue` |
| redis | redis:7-alpine | rate-limit storage |
| gotrue | supabase/gotrue | autoconfirm on, HS256, schema `auth` in the `gotrue` DB |
| supabase-proxy | nginx | maps `/auth/v1/*` to gotrue's root; this is `SUPABASE_URL` |
| api | built from `Dockerfile` | runs `alembic upgrade head` in its own lifespan (same as Render), no separate migration job |

Only the API (`127.0.0.1:${AUDIT_API_PORT:-8000}`) and the Supabase proxy (`127.0.0.1:9998`) are published.

## Start

```sh
docker compose -f docker-compose.audit.yml up -d --build
curl localhost:8000/health && curl localhost:8000/ready
```

If 8000 is taken: `AUDIT_API_PORT=8001 docker compose -f docker-compose.audit.yml up -d --build` (keep the variable set for later compose commands).

Audit from the frontend repo:

```sh
AUDIT_API_BASE_URL=http://localhost:8000/api/v1 npm run audit:full:api
```

## Env and secrets

All values live in `docker-compose.audit.yml` and are throwaway. `SUPABASE_JWT_SECRET` and gotrue's `GOTRUE_JWT_SECRET` are the same value. `SUPABASE_ANON_KEY` is an HS256 JWT with `role=anon` signed with that secret; regenerate after changing the secret:

```sh
docker/audit/make-anon-key.sh '<secret>'
```

CORS allows `http://localhost:4200`, `:3000`, `:3200`; `FRONTEND_URL=http://localhost:3200`. Adjust `ALLOWED_ORIGINS` (JSON list) in the compose file for other ports. `RUN_BACKGROUND_TASKS=false`.

## Clean up

```sh
docker compose -f docker-compose.audit.yml down -v   # stops and deletes the database volume
```

## Known limits

- Rate limits are hardcoded in the routers (`register` 5/minute per client IP, login 10/minute, etc.) and not env-configurable. All requests from the host share one IP, so repeated registers within a minute return 429. Wait a minute or `docker compose -f docker-compose.audit.yml exec redis redis-cli flushall`.
- Email confirmation is off, so register returns 201 with tokens; the confirmation flow is not exercised. No SMTP, password-reset mails, or OAuth providers.
- Tokens are HS256 only; the ES256/JWKS path is not exercised.
- Google Books, Maps, geocoding and Photon calls need external keys/network and are not configured.
- The gotrue image tag is pinned; bump it deliberately.
