# Render deploy

## How deploys work
- `render.yaml` is the Blueprint of the production service `book-club-be` (Docker, free plan, Frankfurt, branch `main`).
- Render does not auto-deploy (`autoDeployTrigger: "off"`). The `deploy-render` job in `.github/workflows/ci-cd.yml` (push to `main`, after lint, tests, build, Docker push and Trivy) deploys the exact commit through the Render API: it looks for an existing deploy of the sha, otherwise `POST /v1/services/<id>/deploys` with `commitId`, then polls until `live`.
- The GitHub deployment status is real: `in_progress` while building, `success` when Render reports `live`, `failure` on `build_failed`/`update_failed`/`pre_deploy_failed`/`canceled`, `deactivated` before live, or a 14 minute timeout. The job fails with it.
- No `checksPass` deadlock: Render no longer waits for GitHub checks, so the job can wait for the deploy.
- Secrets used by the job: `RENDER_API_KEY` (Render account API key) and `RENDER_APP_URL`. `RENDER_DEPLOY_HOOK` is no longer referenced; do not use deploy hooks.
- A failed build or health check (`/health`) leaves the previous instance serving. To retry, re-run the failed `deploy-render` job (it reuses an existing deploy of the sha instead of creating a second one).

## Env vars
- `ENV`, `BACKEND_URL`, `FRONTEND_URL` are fixed in `render.yaml` and must equal the dashboard values; change them in the file.
- `sync: false` vars (`DATABASE_URL`, `SECRET_KEY`, `SUPABASE_*`, `REDIS_URL`, `SENTRY_DSN`, `MAPS_*`, `WEB_REVALIDATE_*`) are asked for only at Blueprint creation, then edited only in the dashboard; new ones must be added by hand.
- Never save a startup-critical var (`DATABASE_URL`, `SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`) empty: the deploy fails and the old instance keeps serving, hiding the problem.
- `SECRET_KEY` is not `generateValue`; a wrong value invalidates all issued tokens.
- The `keep-alive` cron is manual in the dashboard and is not in the Blueprint.

## Baseline (live values read from Render CLI)
branch `main`, rootDir empty, dockerCommand empty, dockerContext `.`, dockerfilePath `Dockerfile`, healthCheckPath `/health`, numInstances 1, plan free, region frankfurt, PR previews off, autoDeploy yes with trigger `commit` (changes to off when the Blueprint is linked), no custom domains. Env: `ENV=production`, `BACKEND_URL=https://book-club-be.onrender.com`, `FRONTEND_URL=https://book-club-planer.vercel.app`.

## Owner procedure: link the Blueprint
Hard rule: link only AFTER `render.yaml` with `autoDeployTrigger: "off"` and the API-deploy `deploy-render` job are on `main` (release PR `develop` -> `main`, merge commit). Render resets omitted options to defaults on first sync, so compare everything first.

1. Merge and release: confirm the final `render.yaml` is on `main`.
2. Record every Settings field of `book-club-be` and copy every env value (all `sync: false` ones included) somewhere safe.
3. Compare with the baseline above and with `render.yaml`; resolve differences before continuing.
4. Dashboard -> Blueprints -> New Blueprint Instance -> this repo, branch `main`.
5. Read the plan preview. Do not apply unless it says "update" for `book-club-be`, creates no new service and the only change is the deploy trigger (`commit` -> `off`). Otherwise stop.
6. Enter the `sync: false` values exactly as copied in step 2. Never type placeholders or leave them empty.
7. Apply, then verify settings and env against the record from step 2, and that the `keep-alive` cron still exists.
8. Merge one trivial commit to `main` and confirm exactly one deploy, created by the `deploy-render` job (not by Render itself) and that the job ends green when it is live.
9. Optional cleanup: delete the `RENDER_DEPLOY_HOOK` GitHub secret and delete/regenerate the deploy hook in Render (Settings -> Deploy Hook); nothing references it. Until the Blueprint is linked the service still auto-deploys on commit; the job then reuses that deploy by sha.

## Rollback
Dashboard -> service -> Events -> pick the last good deploy -> Rollback; then revert the bad commit on `main`. Auto-deploy stays off; the next push to `main` deploys through the job as usual.
