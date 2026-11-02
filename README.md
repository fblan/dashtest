# Kitchen QA — Postgres + Grafana playground

Simulated test campaigns of a "smart kitchen" stored in Postgres, visualised in Grafana.

## Data model
```
campaign (platform = name, e.g. InHouse kitchen / Outdoor kitchen, started_at, ended_at, status,
          kms_version          Kitchen Management System (system under test)
          kes_version          KitEquipmentSimulator
          ktrs_version         Kitchen Test Runner System
          gateway_version      Gateway
          testcampaign_version TestCampaign (test definitions))
 └─ test   (rank, name = NN_scenario, e.g. 03_oven_beef_wellington, status)
     └─ step   (ordered, description, expected_result, actual_result, status)
         └─ action (ordered, description, expected_result, actual_result, status)
```
`status` ∈ `success | skipped | failed`.

Rules:
- When an action fails or is skipped, every later action of the test (rest of the step and all following steps) is `skipped`.
- Parent status (step ← actions, test ← steps, campaign ← tests): any failed → `failed`, else any skipped → `skipped`, else `success`.
  Computed in SQL by `recompute_status()`. Run it again after inserting new data.
- A test run is compared with the previous run of the same test **on the same platform**.

Useful views: `v_campaign` (label + versions changed since the previous campaign on the platform),
`v_test_result` (with `transition`: regression/fixed/… and `changes`), `v_first_failure` (root cause),
`v_campaign_summary`, `v_flakiness`, `status_change(status, base_status)` (regression / fixed / NULL for
one action), `step_diff(test, base_test)` (per step, action by action vs a base run of
the test: actions, actions changed, regressed = passed before and not now, fixed = the reverse), `test_diff(test,
base_test)` (the same summed over the test, with the number of steps concerned),
`v_test_diff` (`test_diff` vs the previous run of the test on the platform), `check_cascade` (must be empty).

## Simulated story
Apr → Oct 2026: 42 campaigns on IN = InHouse kitchen (around 22:00) and 30 on OUT = Outdoor kitchen
(around 10:00), KMS 1.0.0 → 2.5.0.
The other components get their own releases (KES 3.0.0 → 3.2.1, KTRS 5.4.2 → 5.5.1,
Gateway 1.8.3 → 2.0.1, TestCampaign TC-2026.1 → TC-2026.3). Scripted regressions (`generator/scenarios.py`):

| Bug | Component | Introduced | Fixed | Platforms |
|---|---|---|---|---|
| mixer speed controller | KMS | 1.1.0 | 1.2.0 | both |
| oven temperature regulation (Wellington, soufflé, breads) | KMS | 1.3.0 | 1.4.1 | both |
| sous-vide timer (minutes vs hours) | KMS | 2.0.0 | 2.1.0 | both |
| induction power curve (hollandaise, risotto) | KMS | 2.1.0 | 2.1.1 | both |
| fryer oil temperature unstable | KMS | 2.2.0 | 2.4.1 | OUT |
| dishwasher heater firmware | KMS | 2.4.0 | 2.4.1 | IN |
| push notifications dropped (fridge shopping list) | Gateway | 2.0.0 | 2.0.1 | both |
| rotisserie motor simulation (grill skewers) | KES | 3.2.0 | 3.2.1 | OUT |
| steam generator below 100°C (dim sum) | KMS | 2.5.0 | open | both |
| blender motor speed limit (gazpacho) | KMS | 2.5.0 | open | IN |
| grill preheat setpoint (ribeye) | KMS | 2.5.0 | open | OUT |

Plus small random failures and operator skips (ingredient not delivered, module in maintenance, door interlock).

The last 4 campaigns (27 → 31 Oct, `LATEST_CAMPAIGNS`) have no random failures, so they show regression
detection clearly: KMS 2.4.1 + KES 3.2.1 fix the open bugs (all tests pass), then KMS 2.5.0 brings exactly
3 bugs: 2 regressions in the latest campaign of each platform (IN: steamer + blender, OUT: steamer + grill).

Scenarios are written like test procedures (`generator/scenarios.py`): step and action descriptions
can span several lines, and each action lists checks (numeric with tolerance, or qualitative).
Expected/actual results have one line per check; measured values vary from run to run and are
out of tolerance when the action fails.

## Run
Requires Docker (on WSL: Docker Desktop → Settings → Resources → WSL integration enabled for your distro).

```bash
cp .env.example .env   # first time only, then adjust passwords
make up        # start postgres + grafana
make check     # sanity queries
make psql      # SQL shell
make reset     # regenerate data + dashboards, wipe volumes, restart
```
Grafana 12.4.14 (pinned): http://localhost:3000 — anonymous read-only access (role set by `GRAFANA_ANONYMOUS_ROLE`,
default `Viewer`); sign in as admin / admin to edit. Folder **Kitchen QA**:
The three overviews (campaign, test, step) share the same header: a first line with what is shown and the
5 versions of the campaign (a version changed since the previous campaign on the platform is orange ▲), the status,
the pass rate (campaign only) and the duration; a second line with the counts of the items one level down (all /
success / skipped / failed) and the number of those with regressions / fixes. Click a number to filter the list
under it. **Compare with** (top of the page) picks what to compare with. Regression = at least one action passed
in the compared run and does not pass now (skipped and failed both count as not passing); fix = the reverse.
1. **Campaign overview** – header: platform and versions; tests and tests with regressions / fixes. Compared by
   default with the previous campaign on the same platform, or any other campaign of the platform. Per test:
   status, number of steps, steps with regressions and with fixes, duration. Click a test name to open its details.
2. **Trends over time** – one column per platform: tests per campaign over time (total, success, skipped, failed),
   under it the tests with regressions / with fixes compared with the previous campaign on the platform,
   then the campaign duration.
   Hovering a campaign shows its numbers, its 5 versions and the components changed since the previous campaign;
   clicking it offers to open it in the Campaign overview.
4. **Test details** – one test run. Header: test and versions (click to open the campaign); steps and steps with
   regressions / fixes. Compared by default with the previous run of the test on the platform, or any other run
   of the test there (opening a test from the overview keeps the overview's choice). Then the steps (description,
   expected, actual, status, number of actions, actions with regressions, actions with fixes; click a step to open
   it) and the history of the test.
5. **Step details** – one step of a test run, built like Test details. Header: test · step (click to open the test)
   and versions; actions and actions with regressions / fixes. Compared by default with the same step in the
   previous run of the test on the platform. Then the step (description, expected, actual), the actions
   (evolution, status, expected, actual, then for the compared run: status, expected, actual) and the history of
   the step on the platform.

Postgres (local only): `localhost:5432`, db `kitchen`, user `kitchen`/`kitchen` (Grafana uses the read-only role `grafana_ro`, password `GRAFANA_RO_PASSWORD`).

Postgres and Grafana run from images built from `db/Dockerfile` and `grafana/Dockerfile`, which contain
the init scripts, data source and dashboards (`make up` rebuilds them).
Init scripts in `db/init/` only run on an empty volume — use `make reset` after changing them.
Dashboards are generated by `grafana/build_dashboards.py`; edits made in the UI are lost on rebuild,
so export them or change the script.

## Use the dashboards on another Postgres + Grafana (12.x or later)
Everything needed is in 3 places:

| What | File | Notes |
|---|---|---|
| Tables | `db/init/01_schema.sql` | skip it if your database already has these tables (`campaign`, `test`, `step`, `action`, type `exec_status`) |
| Functions and views used by the dashboards | `db/init/02_functions_views.sql` | idempotent: re-run it after every change |
| Dashboards, ready to import | `grafana/export/01_campaign_overview.json`, `02_trends.json`, `04_test_details.json`, `05_step_details.json` | generated by `grafana/build_dashboards.py` |

1. Database (as the owner of the tables):
   ```sh
   psql "$DB_URL" -v ON_ERROR_STOP=1 -f db/init/01_schema.sql          # only on an empty database
   psql "$DB_URL" -v ON_ERROR_STOP=1 -f db/init/02_functions_views.sql
   ```
   Optional read-only user for Grafana (02 grants it SELECT when it exists; run 02 again after creating it):
   ```sql
   CREATE ROLE grafana_ro LOGIN PASSWORD '<password>';
   GRANT USAGE ON SCHEMA public TO grafana_ro;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
   ```
2. Grafana: add a PostgreSQL data source on that database (any name).
3. For each export file: Dashboards → New → Import → paste the file content (or upload it) → **Load** →
   in `DS_KITCHEN` pick the data source → **Import**.

Keep the dashboard uids (`kitchen-overview`, `kitchen-trends`, `kitchen-test`, `kitchen-step`): the links between
dashboards use them. Prefer these files over an export from the Grafana UI: they ask for the data source on import.
The export files are tested by importing them into a Grafana 12.4.14 that only had a PostgreSQL data source.

To change a dashboard: edit `grafana/build_dashboards.py`, run `python3 grafana/build_dashboards.py` (Python 3,
no dependency; it reads the version lists of `generator/scenarios.py` for the Trends tooltip), then import the
regenerated file again (same uid: Grafana asks to overwrite).

## Adapt the dashboards to a database with other table / column names
`tools/adapt_dashboards.py` (Python 3.9+, no dependency) maps your tables onto the dashboards' model without
touching the dashboard SQL: it creates, in a schema of its own (default `kitchen`), 4 views `campaign`, `test`,
`step`, `action` that read your tables with the expected names and types, then the functions and views of
`02_functions_views.sql` on top of them.

1. Write the mapping: `python3 tools/adapt_dashboards.py template > mapping.json` lists every column (with
   its type and whether it is required). For each one give your column name (exact, case included), an SQL
   expression `{"sql": "... src.<column> ..."}`, or `null` when it can be left out:
   - statuses of campaign / test / step are derived from the actions (failed > skipped > success),
     positions from the ids, test rank from the start times, campaign start / end from its tests;
   - `status_values` translates your status values (e.g. `"failed": ["FAIL", "ERROR"]`);
   - `from` is `schema.table`, or a subquery `"(SELECT ... )"` when one table is not enough (joins).
2. Check it against the database (read-only queries):
   `python3 tools/adapt_dashboards.py check mapping.json --psql "postgresql://user@host/db"`
   (or `--grafana <url> --datasource <uid>` to query through Grafana). It reports unknown tables / columns with
   suggestions (typos, case), incompatible types (e.g. epoch numbers for dates, uuid ids), status values not
   translated, then runs the views on your data (nothing is created) and checks NULLs, duplicate ids, orphans,
   duplicate positions or test names (runs are matched by test name, steps / actions by position), dates.
3. Generate: `python3 tools/adapt_dashboards.py build mapping.json --psql "..." --out adapted/` writes
   `adapted/install.sql` (idempotent, one transaction) and `adapted/dashboards/*.json` (the export files, with
   the Trends tooltip values taken from your data).
4. Run `psql "..." -f adapted/install.sql` as a user allowed to create the schema; it grants read access to
   `grafana_role` and sets its `search_path` to the schema (use a dedicated read-only role for Grafana).
   Import the dashboards as described above.

Large databases: derived statuses are computed on every query; map real status columns when you have them,
and index the foreign keys (test → campaign, step → test, action → step).

## Deploy on Dokploy
`docker-compose.yml` is the production file: no published ports, Grafana is served through the
Dokploy domain, Postgres is only reachable by Grafana. (`docker-compose.local.yml` adds the local ports
and is only used through `COMPOSE_FILE` in your local `.env`.)

1. DNS: create an `A` record (e.g. `dash.example.com`) pointing to the VPS.
2. Dokploy → project → **Create Service → Compose**, type **Docker Compose**.
3. Source: GitHub (or Git URL `https://github.com/fblan/dashtest.git`), branch `main`,
   compose path `./docker-compose.yml`.
4. **Environment** tab:
   ```
   PG_USER=kitchen
   PG_PASSWORD=<strong password>
   GRAFANA_RO_PASSWORD=<strong password>
   GRAFANA_ADMIN_PASSWORD=<strong password>
   GRAFANA_ANONYMOUS_ROLE=Viewer
   GRAFANA_ROOT_URL=https://dash.example.com
   ```
   Do not copy `COMPOSE_FILE` / ports from `.env.example`.
5. **Domains** tab → Add Domain: host `dash.example.com`, path `/`, service `grafana`,
   container port `3000`, HTTPS on, certificate Let's Encrypt.
6. **Deploy**, then check the logs of `postgres` (init scripts) and `grafana`.

Notes:
- The database is created on the first deploy (empty `pgdata` volume). After that, the one-shot `migrate`
  service (`db/migrate.sh`, Grafana starts after it succeeds) runs on every deploy: it re-applies the views and
  functions (`db/init/02_functions_views.sql`), and reloads the simulated data when `db/init/03_seed.sql`
  has changed since the last load (all campaigns are replaced). Table changes still need the volume deleted.
- `GRAFANA_ADMIN_PASSWORD` is only applied when the Grafana volume is created; later, change it in Grafana.
- Dashboards come from the image: edit `grafana/build_dashboards.py`, run `make dashboards`, push, redeploy.
