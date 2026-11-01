# Kitchen QA — Postgres + Grafana playground

Simulated test campaigns of a "smart kitchen" stored in Postgres, visualised in Grafana.

## Data model
```
platform (code, name)            IN = InHouse kitchen, OUT = Outdoor kitchen
campaign (platform, started_at, ended_at, status,
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
Apr → Oct 2026: 42 campaigns on IN (around 22:00) and 30 on OUT (around 10:00), KMS 1.0.0 → 2.5.0.
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
1. **Campaign overview** – pick a campaign; per test: status, number of steps, steps with regressions and with
   fixes, duration. Click a test name to open its details. **Compare with** (top of the page) picks the campaign
   to compare with: by default the previous campaign on the same platform, or any other campaign of the platform.
   The comparison row shows that campaign (click to open it), the pass-rate change, and the number of tests with
   regressions and with fixes (click a number to list only those tests). Regression = at least one action passed
   in the compared campaign and does not pass now (skipped and failed both count as not passing); fix = the
   reverse.
2. **Trends over time** – one column per platform: tests per campaign over time (total, success, skipped, failed),
   under it the tests with regressions / with fixes compared with the previous campaign on the platform,
   then the campaign duration.
   Hovering a campaign shows its numbers, its 5 versions and the components changed since the previous campaign;
   clicking it offers to open it in the Campaign overview.
4. **Test details** – one test run: header and versions, then **Compare with** (default: previous run of the test
   on the platform, or any other run of the test there; opening a test from the overview keeps the overview's choice),
   the number of actions with regressions / fixes, the steps (description, expected, actual, status, number of
   actions, actions with regressions, actions with fixes; click a step to open it) and the history of the test.
5. **Step details** – one step of a test run, built like Test details: header and versions, the step (description,
   expected, actual), **Compare with** (default: the same step in the previous run of the test on the platform),
   an actions count tile (all / success / skipped / failed) and actions with regressions / fixes (click a number
   to filter the actions), the actions (evolution, status, expected, actual, then for the compared run: status, expected, actual)
   and the history of the step on the platform.

Postgres (local only): `localhost:5432`, db `kitchen`, user `kitchen`/`kitchen` (Grafana uses the read-only role `grafana_ro`, password `GRAFANA_RO_PASSWORD`).

Postgres and Grafana run from images built from `db/Dockerfile` and `grafana/Dockerfile`, which contain
the init scripts, data source and dashboards (`make up` rebuilds them).
Init scripts in `db/init/` only run on an empty volume — use `make reset` after changing them.
Dashboards are generated by `grafana/build_dashboards.py`; edits made in the UI are lost on rebuild,
so export them or change the script.

## Copy the dashboards to another Grafana (12.x or later)
`grafana/export/*.json` are portable copies (regenerated by `make dashboards`), tested by pasting
them into a Grafana 12.4.14 that only had a PostgreSQL data source.

Prerequisites on the target: a PostgreSQL data source pointing at a database that contains the
tables **and the views** from `db/init/01_schema.sql` + `02_functions_views.sql`.

For each file (01, 02, 04, 05):
1. Open the file, select all, copy.
2. Grafana → Dashboards → New → Import → paste into the text box → **Load**.
3. In `DS_KITCHEN`, pick your PostgreSQL data source → **Import**.

Keep the uids (`kitchen-overview`, `kitchen-test`, …): the links between dashboards use them.
Prefer these files over an export from the Grafana UI: they ask for the data source on import.

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
