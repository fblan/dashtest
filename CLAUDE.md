# Notes for Claude (dashtest — Kitchen QA, Postgres + Grafana)

Repo: https://github.com/fblan/dashtest (public). User-facing docs are in `README.md` (data model,
simulated story, run, export, Dokploy deploy): read it first, this file only adds what you need to work.

## Dev workflow (agreed with the user)
- **Do not run anything locally** (no docker compose up, no local Grafana). Edit, regenerate the
  generated files with python, and test on the Dokploy staging deployment.
- For every change:
  1. branch from `main`: `feature/<short-name>`
  2. edit, then `python3 grafana/build_dashboards.py` (and `python3 generator/generate.py` if the data changes)
  3. commit (end with `Co-Authored-By: ...` line), push the branch, `gh pr create` to `main`
     (body ends with the Claude Code attribution line)
  4. when the work is finished, **force-push it to `staging`**: `git push -f origin HEAD:refs/heads/staging`
  5. the user tests on staging and merges the PR; production (Dokploy) auto-deploys `main`.
- Follow-up fixes for an open PR: commit on the same feature branch, push, force-push to `staging` again.
- Never commit `.env` or real passwords (public repo). `.env.example` holds placeholders only.

## Verify a deployment (anonymous, no password needed)
- Staging Grafana: https://dash.app.staging.dev.asymetrik.org (production URL: ask the user).
- `python3 tools/check_panels.py https://dash.app.staging.dev.asymetrik.org` runs every dashboard query
  through `/api/ds/query` and prints ok/error per variable, annotation and panel. Add new variables to
  `VARS` in that script when you create them.
- Ad-hoc SQL: POST to `<url>/api/ds/query` with datasource uid `kitchen-pg`, `rawSql`, `format: table`.
- It checks SQL only, not the layout: ask the user to look at the page.

## Layout
- `db/init/01_schema.sql` tables (platform, campaign, test, step, action), enum `exec_status`.
- `db/init/02_functions_views.sql` functions + views. **Must stay idempotent** (`DROP VIEW IF EXISTS … CASCADE`
  at the top listing every view, `DROP FUNCTION IF EXISTS` for functions returning a table, `CREATE OR REPLACE FUNCTION`, `GRANT SELECT … TO grafana_ro` at the end):
  the `migrate` service re-applies it on every deploy. Add any new view to the DROP list.
- `db/init/03_seed.sql` is generated (1.7 MB) by `generator/generate.py` from `generator/scenarios.py`.
  Init scripts 00/01/03 only run on an empty volume. On later deploys `db/migrate.sh` reloads 03 when its
  sha256 differs from `seed_info` (TRUNCATE + load in one transaction), so data changes deploy by themselves.
  Schema (table) changes on an existing DB need the volume deleted or an explicit migration — none exists yet.
  History must stay reproducible: new campaigns go in `LATEST_CAMPAIGNS` (fixed dates, no random noise),
  appended after the generated schedule so the earlier campaigns are unchanged.
- `grafana/build_dashboards.py` generates `grafana/dashboards/*.json` (provisioned) and `grafana/export/*.json`
  (portable copies to paste in another Grafana 12). Never hand-edit the JSON; always commit both.
- `docker-compose.yml` = production (no ports; Dokploy domain → service `grafana`, port 3000).
  Services: postgres (built from `db/`), migrate (one-shot `db/migrate.sh`), grafana (built from `grafana/`,
  waits for migrate). `docker-compose.local.yml` only adds ports (used locally through `COMPOSE_FILE`).
- `tools/check_panels.py` remote query checker (above).

## Domain rules
- status `success | skipped | failed`; after a failed/skipped action, every later action of the test is skipped.
- parent status: failed > skipped > success, computed by `recompute_status()`.
- Platforms IN (InHouse kitchen) / OUT (Outdoor kitchen). Every comparison (previous campaign, transitions,
  diffs, history, flakiness) is **per platform**: window `PARTITION BY [name,] platform ORDER BY started_at`.
- 5 versions per campaign: KMS (system under test), KES, KTRS, Gateway, TestCampaign — shown on all views.
- Test names `NN_<scenario>`; no subsystem concept (removed on request).

## Dashboards (uids are used by links, keep them)
- `kitchen-overview` Campaign overview: vars platform, campaign, `compare` (0 = previous campaign on the platform,
  else a campaign id of the same platform; SQL resolves it with `BASE` in build_dashboards.py and matches tests by
  name, then `test_diff()`), hidden `test_status` and `change` (all / regression / fix). Status tiles and the regressions / fixes tile filter the
  tests table; each click sets one filter and clears the other (combined filters confused the user).
  Comparison row vs the `compare` campaign. Links keep `${compare:queryparam}`.
  User's definitions (action level, not test status): regression = an action passed before and does not pass
  now (skipped/failed = not passing), fix = the reverse; tiles count tests. Table columns (user's choice):
  name, status, steps, steps with regressions, steps with fixes, duration.
- `kitchen-test` Test details: vars platform, test, `compare` (0 = previous run of the test on the platform, else
  a test id of another run of the same test; resolved by `BASE_TEST`). Header rows, comparison row, steps table
  (description, expected, actual, status, actions, actions with regressions / fixes from `step_diff()`), history.
  `test_link(..., compare=)` passes the compared run (the overview passes the test of its compared campaign).
  Clicking a step (# or description) opens Step details with the step of the compared run (`STEP_URL`).
- `kitchen-step` Step details: vars platform, test, step (steps of the test), `compare` (0 = same step in the
  previous run, else a step id at the same position in another run of the test; resolved by `BASE_STEP`).
  Hidden filters `action_status` / `change` set by the actions count tile and the regressions / fixes tile
  (helpers `status_filters`, `change_filter`, `filter_vars`, shared with the overview).
  Header rows, step text + actions count tile, comparison row, actions table (evolution = `status_change()`, status, expected, actual | compared status, expected, actual), step history.
- `kitchen-trends` Trends over time: var platform without "All" (user's choice; IN and OUT selected by default); 3 panels repeated per platform (horizontal): tests per campaign
  (total, success, skipped, failed), tests with regressions / fixes vs previous campaign (`v_test_diff`), campaign duration.
  Versions + changed components in the tooltip only: string fields → enum (convertFieldType needs
  `enumConfig.text`, taken from generator/scenarios.py releases: rebuild dashboards when they change), drawn invisible
  (lineWidth 0, no points, axis hidden, transparent) and hidden from legend. hideFrom.viz drops them from the tooltip.
  No release annotations (removed on request).
- The Regressions dashboard (`kitchen-regressions`, 03) was removed on request (too complex); don't bring it back.
- Anonymous Viewer access must stay on (dashboards visible without login).

## Grafana gotchas (Grafana 12.4.14, pinned — target is 12.x)
- Single-value variables are not quoted in SQL: write `'$base'`; multi-value/All are quoted automatically.
- Repeated panels: use `${platform:sqlstring}`. Links: `${var:queryparam}`, `${__url_time_range}`,
  `${__data.fields.x}`; encode `$__all` as `%24__all` in URLs.
- Stat text size via `options.text.{titleSize,valueSize}`; table wrap via `custom.wrapText`.
- Datasource type `grafana-postgresql-datasource`, uid `kitchen-pg`, user `grafana_ro` (read-only).

## User preferences
- Prefers concise answers and action over long option lists.
