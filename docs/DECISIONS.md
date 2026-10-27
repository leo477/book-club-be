# Decisions

## Randomizer winner is chosen client-side (SCRUM-181)

Status: accepted.

`POST /api/v1/clubs/{club_id}/randomizer/sessions` records a draw; it does not perform one. The client picks the winner and sends it as `result`. The server only checks that every candidate is a member of the club, that candidates are unique, and, when present, that `result` is one of the candidates. Stored candidates and result are rebuilt from the database, so the server overwrites the client's `displayName` and `avatarUrl` with the DB values. It does not verify that the pick was random or unbiased.

Why this is acceptable: the randomizer is a convenience for a book club to choose a book or host among its own members, run by an organizer. Nothing of value depends on it, and the organizer who submits the result could equally announce any winner without using the feature. Server-side selection would not remove that trust (the organizer decides whether to use the outcome) and would cost the in-browser reveal animation, which needs the result before it plays.

Revisit if a draw ever gates something with real stakes (prizes, moderation outcomes, or anything other members cannot overrule). The change would be to stop accepting `result` and have the server set it with `secrets.choice` from the validated candidates.

## CSP / Trusted Types report receiver (SCRUM-67)

Status: accepted.

`POST /api/v1/analytics/csp-report` collects Report-Only violations. It always answers 204 (no oracle), reads the body itself (browsers send `application/reports+json` or `application/csp-report`, which pydantic bodies reject), caps it at 8 KB and 10 reports, and rate-limits to 30/minute per IP plus a per-process cap of 500 upserted rows per hour.

Only whitelisted fields are kept, normalised: URL query/fragment dropped, UUIDs and 5+ digit runs in the route replaced by `:id`, blocked URL reduced to a keyword, `self` (the document's own host), a host from a short known allow-list (FE CSP hosts plus the Supabase and backend hosts from settings), a scheme name (`data`, `blob`, `filesystem`) or `other` (forged hosts and `about:`/`chrome-extension:` cannot mint rows; the first `sample` keeps the detail), sample cut to 120 chars with emails and URLs scrubbed. IP, cookies and User-Agent are never stored. Reports whose document host is not in `ALLOWED_ORIGINS` / `CORS_ORIGIN_REGEX` (localhost only outside production) are dropped silently. Rows are daily aggregates in `csp_reports` (unique on day, env, route, directive, disposition, sink, blocked, policy_name) with a `count` bumped by upsert; `cleanup_csp_reports` deletes rows older than 30 days. Migration `z6a7b8c9d0e1` is applied manually (`uv run alembic upgrade head`).

Key cardinality is bounded because every field is attacker-controlled: `directive` is allow-listed (unknown becomes `other`), `route` keeps at most two segments (deeper becomes `*`, a non-id second segment becomes `:x`, an unknown top-level segment becomes `/other`), `blocked` is a keyword, `self`, a known host, a scheme name or `other`, `sink` is allow-listed to the real DOM sinks and `policy_name` to the FE trusted-types policies (anything else becomes `other`, absent stays empty), the body read has a 3 s timeout, and the hourly budget of 500 is per process and consumed by attempted rows, so N workers allow N x 500 per hour. The `prod` vs `preview` label depends on `ALLOWED_ORIGINS` containing the production host; the owner must verify that env var, otherwise production reports are labelled `preview`.

Read queries:

```sql
-- top violations, last 7 days
SELECT env, directive, sink, blocked, policy_name, route, SUM(count) AS n
FROM csp_reports WHERE day >= current_date - 7
GROUP BY 1,2,3,4,5,6 ORDER BY n DESC LIMIT 50;

-- daily trend per directive on prod
SELECT day, directive, SUM(count) AS n FROM csp_reports
WHERE env = 'prod' GROUP BY 1,2 ORDER BY day DESC, n DESC;

-- Trusted Types sinks / policies seen (enforcement readiness)
SELECT sink, policy_name, SUM(count) AS n, MIN(sample) AS example
FROM csp_reports WHERE directive IN ('require-trusted-types-for','trusted-types')
GROUP BY 1,2 ORDER BY n DESC;
```
