# Render deploy

## How deploys work
- `render.yaml` is the Blueprint of the production service `book-club-be` (Docker, free plan, Frankfurt, branch `main`).
- Render deploys `main` automatically, but only after the commit's GitHub checks pass (`autoDeployTrigger: checksPass`). This is the only deploy path.
- The `deploy-render` job in `.github/workflows/ci-cd.yml` only records a GitHub deployment as `queued`. It does not deploy and does not prove the deploy succeeded; check the Render dashboard (Events / Logs).
- A failed build or failed health check (`/health`) leaves the previous instance serving.

## Owner steps: link the Blueprint
1. Render dashboard -> Blueprints -> New Blueprint Instance.
2. Pick this repo, branch `main`, confirm `render.yaml`.
3. Render matches the existing service by name `book-club-be`. Check the plan preview shows an update of the existing service, not a new one.
4. Render asks for the `sync: false` values on creation. Leave the existing values untouched / re-enter the same ones; never submit empty ones.
5. Apply.

## Environment variables
- `ENV`, `BACKEND_URL`, `FRONTEND_URL` are fixed in `render.yaml`; change them there.
- Everything with `sync: false` (`DATABASE_URL`, `SECRET_KEY`, `SUPABASE_*`, `REDIS_URL`, `SENTRY_DSN`, `MAPS_*`, `WEB_REVALIDATE_*`) is edited only in the dashboard (Environment). Render ignores them on later Blueprint syncs, so a new secret must also be added by hand.
- Never save a startup-critical variable (`DATABASE_URL`, `SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`) empty: the new deploy fails at startup and the old instance keeps serving, hiding the problem.
- `SECRET_KEY` is not `generateValue`, so it can never be regenerated (that would invalidate issued tokens).
- The `keep-alive` cron stays manual in the dashboard (see comment in `render.yaml`).

## Rollback
Dashboard -> service -> Events -> pick the last good deploy -> Rollback. Then revert the bad commit on `main`. Note that a rollback turns off auto-deploy until it is re-enabled on the service; re-enable it after the fix.

## Post-link checklist
- [ ] Service shows as Blueprint-managed.
- [ ] No duplicate `book-club-be` service was created.
- [ ] Service settings still show: Docker, Free, Frankfurt, branch `main`, health check `/health`, auto-deploy "After CI checks pass", PR previews off.
- [ ] Env vars unchanged and non-empty.
- [ ] A test merge deploys once (one deploy in Events, none from a hook).
- [ ] Delete the `RENDER_DEPLOY_HOOK` GitHub secret and regenerate/delete the hook URL in Render (Settings -> Deploy Hook). `RENDER_APP_URL` is still used.
