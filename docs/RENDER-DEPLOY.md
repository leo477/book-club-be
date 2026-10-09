# Render deploy

## How deploys work
- `render.yaml` is the Blueprint of the production service `book-club-be` (Docker, free plan, Frankfurt, branch `main`).
- Render deploys `main` only after every GitHub check of the commit passes (`autoDeployTrigger: checksPass`). This is the only deploy path once the deploy hook is removed.
- The `deploy-render` job in `.github/workflows/ci-cd.yml` only records a GitHub deployment as `queued`. It does not deploy and does not prove success; check the Render dashboard (Events / Logs).
- A failed build or health check (`/health`) leaves the previous instance serving.
- `checksPass` waits for ALL checks of the commit: `ci.yml`, CodeQL and every `ci-cd.yml` job, including `docker-build-push` and `trivy-scan`. A Docker Hub outage, a Trivy CRITICAL finding, or a run cancelled by `concurrency: cancel-in-progress` silently skips the production deploy. If a deploy did not start, check the commit's checks first (re-run the failed ones).
- Unverified: whether jobs blocked by `needs:` show up as pending checks early (otherwise Render could deploy before they finish). Confirm with the test merge in step 8.

## Env vars
- `ENV`, `BACKEND_URL`, `FRONTEND_URL` are fixed in `render.yaml` and must equal the dashboard values; change them in the file.
- `sync: false` vars (`DATABASE_URL`, `SECRET_KEY`, `SUPABASE_*`, `REDIS_URL`, `SENTRY_DSN`, `MAPS_*`, `WEB_REVALIDATE_*`) are asked for only at Blueprint creation, then edited only in the dashboard; new ones must be added by hand.
- Never save a startup-critical var (`DATABASE_URL`, `SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`) empty: the deploy fails and the old instance keeps serving, hiding the problem.
- `SECRET_KEY` is not `generateValue`; a wrong value invalidates all issued tokens.
- The `keep-alive` cron is manual in the dashboard and is not in the Blueprint.

## Baseline (live values read from Render CLI)
branch `main`, rootDir empty, dockerCommand empty, dockerContext `.`, dockerfilePath `Dockerfile`, healthCheckPath `/health`, numInstances 1, plan free, region frankfurt, PR previews off, autoDeploy yes with trigger `commit`, no custom domains. Env: `ENV=production`, `BACKEND_URL=https://book-club-be.onrender.com`, `FRONTEND_URL=https://book-club-planer.vercel.app`.

## Owner procedure: link the Blueprint
Hard rule: link only AFTER `render.yaml` with `autoDeployTrigger: checksPass` is on `main` (release PR `develop` -> `main`, merge commit). Render resets omitted options to defaults on first sync, so compare everything first.

1. Merge and release: confirm the final `render.yaml` is on `main`.
2. Record every Settings field of `book-club-be` and copy every env value (all `sync: false` ones included) somewhere safe.
3. Compare with the baseline above and with `render.yaml`; resolve differences before continuing.
4. Dashboard -> Blueprints -> New Blueprint Instance -> this repo, branch `main`.
5. Read the plan preview. Do not apply unless it says "update" for `book-club-be`, creates no new service and the only change is the deploy trigger (`commit` -> `checksPass`). Otherwise stop.
6. Enter the `sync: false` values exactly as copied in step 2. Never type placeholders or leave them empty.
7. Apply, then verify settings and env against the record from step 2, and that the `keep-alive` cron still exists.
8. Merge one trivial commit to `main` and confirm exactly one deploy that starts after all checks finished.
9. Required, only after that first successful Blueprint deploy: delete the `RENDER_DEPLOY_HOOK` GitHub secret and delete/regenerate the deploy hook in Render (Settings -> Deploy Hook). Until then the hook is a live second deploy path. `RENDER_APP_URL` is still used.

## Rollback
Dashboard -> service -> Events -> pick the last good deploy -> Rollback; then revert the bad commit on `main`. A dashboard rollback disables auto-deploy. The repo owner must re-enable it (Settings -> Build & Deploy -> Auto-Deploy "After CI checks pass") after the fix. The next Blueprint sync may also reset the trigger, so recheck it after any sync.
