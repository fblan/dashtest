#!/usr/bin/env python3
"""Adapt the Kitchen QA dashboards to an existing database whose tables and columns have other names.

The dashboard SQL is not rewritten. A mapping file (JSON) says where each column of the dashboards' model
(campaign -> test -> step -> action, see db/init/01_schema.sql) is in the real database; the script generates:
  - install.sql: a schema (default "kitchen") with 4 views named campaign, test, step, action that read the real
    tables and expose the expected names and types, then the functions and views of db/init/02_functions_views.sql
    on top of them, and the grants / search_path for Grafana's role;
  - dashboards/*.json: the export files of grafana/export, with the Trends tooltip values (versions, platforms)
    taken from the real data.

Before generating, it checks the mapping: unknown or missing names (with suggestions), and, when it can reach the
database (--psql or --grafana), that tables and columns exist with compatible types, that the mapped views run, and
the data the dashboards rely on (status values, ids, duplicates, orphans, dates).

  python3 tools/adapt_dashboards.py template > mapping.json            # every column, to fill in
  python3 tools/adapt_dashboards.py check mapping.json --psql "postgresql://user@host/db"
  python3 tools/adapt_dashboards.py build mapping.json --psql "..." --out adapted/
  (or --grafana https://grafana.example.com --datasource <uid>: queries through Grafana, read-only;
   token in GRAFANA_TOKEN if anonymous access is off)

Python 3.9+, no dependency (--psql needs the psql client, version 12 or later).
"""
import argparse
import csv
import difflib
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWS_SQL = ROOT / "db/init/02_functions_views.sql"
EXPORT = ROOT / "grafana/export"
STATUSES = ("success", "skipped", "failed")
COMPONENTS = [("KMS", "kms_version"), ("KES", "kes_version"), ("KTRS", "ktrs_version"),
              ("Gateway", "gateway_version"), ("TestCampaign", "testcampaign_version")]

# The model the dashboards read: table -> column -> (type, default, description).
# default: REQUIRED (must be mapped), DERIVE (computed from the other columns when not mapped) or an SQL value.
REQUIRED, DERIVE = "required", "derive"
MODEL = {
    "campaign": {
        "id": ("int", REQUIRED, "unique id of the campaign"),
        "platform": ("text", REQUIRED, "platform name; every comparison is per platform"),
        "kms_version": ("text", REQUIRED, "version of the system under test (KMS)"),
        "kms_version_order": ("int", "NULL", "numeric order of KMS versions (not used by the dashboards)"),
        "kes_version": ("text", "'-'", "KES version"),
        "ktrs_version": ("text", "'-'", "KTRS version"),
        "gateway_version": ("text", "'-'", "Gateway version"),
        "testcampaign_version": ("text", "'-'", "TestCampaign version"),
        "started_at": ("timestamptz", DERIVE, "start (default: first test start)"),
        "ended_at": ("timestamptz", DERIVE, "end (default: last test end)"),
        "status": ("exec_status", DERIVE, "status (default: from the tests: failed > skipped > success)"),
    },
    "test": {
        "id": ("int", REQUIRED, "unique id of the test execution"),
        "campaign_id": ("int", REQUIRED, "campaign of the test"),
        "rank": ("smallint", DERIVE, "order in the campaign (default: by start time)"),
        "name": ("text", REQUIRED, "test name; runs of the same test are matched by name"),
        "started_at": ("timestamptz", REQUIRED, "start"),
        "ended_at": ("timestamptz", REQUIRED, "end"),
        "status": ("exec_status", DERIVE, "status (default: from the steps)"),
    },
    "step": {
        "id": ("int", REQUIRED, "unique id of the step"),
        "test_id": ("int", REQUIRED, "test of the step"),
        "position": ("smallint", DERIVE, "order in the test (default: by id); steps are matched by position"),
        "description": ("text", REQUIRED, "description"),
        "expected_result": ("text", "''", "expected result"),
        "actual_result": ("text", "NULL", "actual result"),
        "status": ("exec_status", DERIVE, "status (default: from the actions)"),
    },
    "action": {
        "id": ("int", REQUIRED, "unique id of the action"),
        "step_id": ("int", REQUIRED, "step of the action"),
        "position": ("smallint", DERIVE, "order in the step (default: by id); actions are matched by position"),
        "description": ("text", REQUIRED, "description"),
        "expected_result": ("text", "''", "expected result"),
        "actual_result": ("text", "NULL", "actual result"),
        "status": ("exec_status", REQUIRED, "status: success / skipped / failed (see status_values)"),
        "duration_ms": ("int", "0", "duration in milliseconds"),
    },
}
ORDER = ["action", "step", "test", "campaign"]      # creation order: each view may derive from the previous one
CHILD = {"step": ("action", "step_id"), "test": ("step", "test_id"), "campaign": ("test", "campaign_id")}
PARENT = {"test": ("campaign", "campaign_id"), "step": ("test", "test_id"), "action": ("step", "step_id")}
TOP_KEYS = {"schema", "grafana_role", "status_values", *MODEL}

# information_schema.columns.data_type values accepted for each target type: ok without remark, else warning
INT_TYPES = {"smallint", "integer", "bigint"}
TEXT_TYPES = {"text", "character varying", "character", "USER-DEFINED"}


def num(value):
    return int(float(value or 0))


def qi(name):
    """Quoted SQL identifier (names are used exactly as written, case included)."""
    return '"' + name.replace('"', '""') + '"'


def ql(value):
    return "'" + str(value).replace("'", "''") + "'"


def suggest(name, choices):
    close = difflib.get_close_matches(name, list(choices), n=3, cutoff=0.6)
    return f" (did you mean {', '.join(close)}?)" if close else ""


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, where, msg):
        self.items.append((level, where, msg))

    def error(self, where, msg):
        self.add("ERROR", where, msg)

    def warn(self, where, msg):
        self.add("WARN", where, msg)

    def info(self, where, msg):
        self.add("INFO", where, msg)

    @property
    def errors(self):
        return sum(1 for level, *_ in self.items if level == "ERROR")

    def print(self):
        for level in ("ERROR", "WARN", "INFO"):
            for lv, where, msg in self.items:
                if lv == level:
                    print(f"{level:5}  {where:28}  {msg}")
        n = {lv: sum(1 for x, *_ in self.items if x == lv) for lv in ("ERROR", "WARN", "INFO")}
        print(f"\n{n['ERROR']} error(s), {n['WARN']} warning(s), {n['INFO']} info")


# ---------------------------------------------------------------- database access (read-only queries)
class Psql:
    def __init__(self, dsn):
        self.dsn = dsn

    def query(self, sql):
        r = subprocess.run(["psql", self.dsn, "-X", "-q", "--csv", "-v", "ON_ERROR_STOP=1", "-c", sql],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr.strip())
        rows = list(csv.reader(io.StringIO(r.stdout)))
        return rows[1:]


class Grafana:
    def __init__(self, url, uid, token=None):
        self.url, self.uid, self.token = url.rstrip("/"), uid, token

    def query(self, sql):
        body = {"from": "now-1h", "to": "now", "queries": [{
            "refId": "A", "datasource": {"uid": self.uid}, "rawSql": sql, "format": "table", "rawQuery": True}]}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url + "/api/ds/query", json.dumps(body).encode(), headers)
        try:
            res = json.load(urllib.request.urlopen(req))["results"]["A"]
        except urllib.error.HTTPError as e:
            res = json.loads(e.read() or b"{}").get("results", {}).get("A", {"error": f"HTTP {e.code}"})
        if "error" in res:
            raise RuntimeError(res["error"])
        frames = res.get("frames") or []
        values = frames[0]["data"].get("values") if frames else None
        if not values:
            return []
        return [[None if v is None else str(v) for v in row] for row in zip(*values)]


# ---------------------------------------------------------------- mapping
class Mapping:
    def __init__(self, data):
        self.data = data
        self.schema = data.get("schema", "kitchen")
        self.role = data.get("grafana_role")
        self.status_values = data.get("status_values")

    def source(self, table):
        """(schema, table) of the real table, or None for a subquery."""
        src = self.data[table]["from"]
        if src.lstrip().startswith("("):
            return None
        parts = src.split(".")
        return (parts[0], parts[1]) if len(parts) == 2 else ("public", parts[0])

    def from_sql(self, table):
        s = self.source(table)
        return f"{qi(s[0])}.{qi(s[1])}" if s else self.data[table]["from"]

    def column(self, table, col):
        """Mapped value: a column name (str), {"sql": expression}, or None (not mapped)."""
        return self.data[table].get("columns", {}).get(col)

    def raw(self, table, col):
        """SQL expression of the mapped column, alias src, or None when not mapped."""
        v = self.column(table, col)
        if v is None:
            return None
        return f"src.{qi(v)}" if isinstance(v, str) else f"({v['sql']})"


def check_structure(m, rep):
    d = m.data
    for key in d:
        if key not in TOP_KEYS and not key.startswith("_"):
            rep.error(key, "unknown key" + suggest(key, TOP_KEYS))
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", m.schema):
        rep.error("schema", f"{m.schema!r}: use lower case letters, digits and _")
    if m.status_values is not None:
        seen = {}
        if not isinstance(m.status_values, dict):
            rep.error("status_values", 'expected {"success": [...], "skipped": [...], "failed": [...]}')
        else:
            for k, vals in m.status_values.items():
                if k not in STATUSES:
                    rep.error("status_values", f"{k!r} is not one of {', '.join(STATUSES)}" + suggest(k, STATUSES))
                    continue
                if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
                    rep.error(f"status_values.{k}", "expected a list of strings")
                    continue
                for v in vals:
                    if v in seen:
                        rep.error(f"status_values.{k}", f"{v!r} is also listed under {seen[v]}")
                    seen[v] = k
            for k in STATUSES:
                if k not in m.status_values:
                    rep.warn("status_values", f"no value for {k}")
    for table, cols in MODEL.items():
        if table not in d:
            rep.error(table, "missing table mapping")
            continue
        t = d[table]
        if not isinstance(t, dict) or not isinstance(t.get("from"), str):
            rep.error(table, 'expected {"from": "schema.table", "columns": {...}}')
            continue
        for key in t:
            if key not in ("from", "columns") and not key.startswith("_"):
                rep.error(table, f"unknown key {key!r}" + suggest(key, ("from", "columns")))
        src = m.source(table)
        if src and len(t["from"].split(".")) > 2:
            rep.error(f"{table}.from", f"{t['from']!r}: expected table or schema.table")
        if src and src[0] == m.schema and src[1] == table:
            rep.error(f"{table}.from", f"{t['from']} would be replaced by the view {m.schema}.{table}: "
                                       f"choose another \"schema\"")
        mapped = t.get("columns", {})
        if not isinstance(mapped, dict):
            rep.error(f"{table}.columns", "expected an object {model column: real column}")
            continue
        for col, v in mapped.items():
            if col.startswith("_"):
                continue
            if col not in cols:
                rep.error(f"{table}.{col}", "not a column of the dashboards' model" + suggest(col, cols))
            elif not (v is None or isinstance(v, str) or (isinstance(v, dict) and isinstance(v.get("sql"), str))):
                rep.error(f"{table}.{col}", 'expected a column name, {"sql": "expression"} or null')
        for col, (_, default, desc) in cols.items():
            if mapped.get(col) is None:
                if default == REQUIRED:
                    rep.error(f"{table}.{col}", f"required, not mapped ({desc})")
                elif col == "status" or col in ("started_at", "ended_at", "rank", "position"):
                    rep.info(f"{table}.{col}", f"not mapped: {desc}")
                else:
                    rep.info(f"{table}.{col}", f"not mapped: {default}")
        names = [v for v in mapped.values() if isinstance(v, str)]
        for v in sorted({v for v in names if names.count(v) > 1}):
            rep.warn(table, f"column {v!r} is mapped several times")


# ---------------------------------------------------------------- SQL of the compatibility views
def status_sql(m, raw, typ):
    """Mapped status expression -> success / skipped / failed (NULL for a value not listed)."""
    if m.status_values:
        whens = " ".join(f"WHEN {raw}::text IN ({', '.join(ql(v) for v in vals)}) THEN {ql(k)}"
                         for k, vals in m.status_values.items() if vals)
        return f"(CASE {whens} END)::{typ}"
    return f"({raw}::text)::{typ}"


def view_sql(m, table, ref, status_type="exec_status"):
    """SELECT of the view `table`: real columns renamed and cast; `ref(t)` = how to reference another view."""
    cols = []
    idx = m.raw(table, "id")
    for col, (typ, default, _) in MODEL[table].items():
        raw = m.raw(table, col)
        typ_sql = status_type if typ == "exec_status" else typ
        if col == "status" and raw is not None:
            expr = status_sql(m, raw, typ_sql)
        elif raw is not None:
            expr = f"({raw})::{typ_sql}"
        elif col == "status":
            child, fk = CHILD[table]
            expr = (f"(SELECT CASE WHEN bool_or(x.status = 'failed') THEN 'failed' "
                    f"WHEN bool_or(x.status = 'skipped') THEN 'skipped' ELSE 'success' END "
                    f"FROM {ref(child)} x WHERE x.{fk} = ({idx})::int)::{typ_sql}")
        elif col in ("started_at", "ended_at"):
            agg = "min" if col == "started_at" else "max"
            expr = f"(SELECT {agg}(x.{col}) FROM {ref('test')} x WHERE x.campaign_id = ({idx})::int)"
        elif col == "rank":
            expr = (f"(row_number() OVER (PARTITION BY {m.raw(table, 'campaign_id')} "
                    f"ORDER BY {m.raw(table, 'started_at')}, {m.raw(table, 'name')}))::smallint")
        elif col == "position":
            parent_fk = PARENT[table][1]
            expr = f"(row_number() OVER (PARTITION BY {m.raw(table, parent_fk)} ORDER BY {idx}))::smallint"
        else:
            expr = f"{default}::{typ_sql}"
        cols.append(f"{expr} AS {col}")
    return "SELECT " + ",\n       ".join(cols) + f"\nFROM {m.from_sql(table)} src"


def cte_sql(m):
    """The 4 views as CTEs (dry run on the real database, nothing created): WITH action AS (...), ..."""
    return "WITH " + ",\n".join(f"{t} AS (\n{view_sql(m, t, qi, 'text')}\n)" for t in ORDER) + "\n"


def install_sql(m):
    s = m.schema
    out = [f"-- GENERATED by tools/adapt_dashboards.py: views of the Kitchen QA dashboards in schema {s}.",
           "-- Idempotent: run it again after changing the mapping or the dashboards' views.",
           "BEGIN;", f"CREATE SCHEMA IF NOT EXISTS {qi(s)};", f"SET LOCAL search_path = {qi(s)};",
           f"DROP VIEW IF EXISTS {', '.join(f'{qi(s)}.{qi(t)}' for t in ORDER)} CASCADE;",
           f"DO $$ BEGIN CREATE TYPE {qi(s)}.exec_status AS ENUM ({', '.join(ql(x) for x in STATUSES)});",
           "EXCEPTION WHEN duplicate_object THEN NULL; END $$;", ""]
    for t in ORDER:
        out += [f"CREATE VIEW {qi(s)}.{qi(t)} AS", view_sql(m, t, lambda v: f"{qi(s)}.{qi(v)}") + ";", ""]
    views = VIEWS_SQL.read_text()
    # recompute_status() updates the tables: not applicable on views (statuses are mapped or derived above)
    views, n = re.subn(r"CREATE OR REPLACE FUNCTION recompute_status\(\).*?\n\$\$;\n", "", views, flags=re.S)
    grant = re.search(r"-- read access for Grafana's role.*?END \$\$;\n", views, flags=re.S)
    if n != 1 or not grant:
        raise SystemExit(f"{VIEWS_SQL}: recompute_status() or the grant block not found, update this script")
    views = views.replace(grant.group(0), "")
    out += [f"-- {VIEWS_SQL.relative_to(ROOT)}", views.strip(), ""]
    if m.role:
        r = qi(m.role)
        out += [f"GRANT USAGE ON SCHEMA {qi(s)} TO {r};", f"GRANT SELECT ON ALL TABLES IN SCHEMA {qi(s)} TO {r};",
                "-- the dashboards' SQL uses unqualified names", f"ALTER ROLE {r} SET search_path = {qi(s)}, public;"]
    out += ["COMMIT;", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- checks against the database
def check_database(m, db, rep):
    """Names and types from information_schema, then a dry run of the views and data checks. Returns the
    values for the Trends tooltip, or None when the views do not run."""
    try:
        rows = db.query("SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')")
    except RuntimeError as e:
        rep.error("database", f"cannot read information_schema: {e}")
        return None
    tables = {}
    for sch, tab, col, typ in rows:
        tables.setdefault((sch, tab), {})[col] = typ
    for table in MODEL:
        if isinstance(m.data.get(table), dict) and isinstance(m.data[table].get("from"), str) \
                and isinstance(m.data[table].get("columns", {}), dict):
            check_table(m, table, tables, rep)
    for (sch, tab) in tables:
        if sch == m.schema and tab in MODEL:
            kind = db.query(f"SELECT table_type FROM information_schema.tables WHERE table_schema = {ql(sch)} "
                            f"AND table_name = {ql(tab)}")
            if kind and kind[0][0] != "VIEW":
                rep.error("schema", f"{sch}.{tab} is a table: install.sql would replace it by a view, "
                                    f"choose another \"schema\"")
    if m.role:
        if not db.query(f"SELECT 1 FROM pg_roles WHERE rolname = {ql(m.role)}"):
            rep.error("grafana_role", f"role {m.role!r} does not exist")
    if rep.errors:
        rep.info("database", "dry run of the views skipped: fix the errors first")
        return None
    return check_data(m, db, rep)


def check_table(m, table, tables, rep):
    src = m.source(table)
    if src is None:
        rep.info(f"{table}.from", "subquery: names checked by the dry run only")
        return
    if src not in tables:
        same_case = [k for k in tables if (k[0].lower(), k[1].lower()) == (src[0].lower(), src[1].lower())]
        elsewhere = [k for k in tables if k[1] == src[1]]
        if same_case:
            hint = f" (case differs: {same_case[0][0]}.{same_case[0][1]}; names are used exactly as written)"
        elif elsewhere:
            hint = f" (found in schema {', '.join(k[0] for k in elsewhere)})"
        else:
            hint = suggest(src[1], {k[1] for k in tables if k[0] == src[0]})
        rep.error(f"{table}.from", f"table {src[0]}.{src[1]} not found{hint}")
        return
    real = tables[src]
    for col, (typ, *_rest) in MODEL[table].items():
        v = m.column(table, col)
        where = f"{table}.{col}"
        if not isinstance(v, str):
            continue
        if v not in real:
            ci = [c for c in real if c.lower() == v.lower()]
            hint = f" (case differs: {ci[0]}; names are used exactly as written)" if ci else suggest(v, real)
            rep.error(where, f"column {v!r} not in {src[0]}.{src[1]}{hint}")
            continue
        check_type(where, v, real[v], typ, rep)


def check_type(where, col, actual, target, rep):
    if target in ("int", "smallint"):
        if actual in INT_TYPES:
            if actual == "bigint" or (actual == "integer" and target == "smallint"):
                rep.warn(where, f"{col} is {actual}, cast to {target}: values out of range make the views fail")
        elif actual in ("numeric", "real", "double precision"):
            rep.warn(where, f"{col} is {actual}, cast to {target} (rounded)")
        elif actual in ("text", "character varying", "character"):
            rep.warn(where, f"{col} is {actual}, cast to {target}: fails on non-numeric values")
        else:
            rep.error(where, f"{col} is {actual}, {target} expected "
                             f"(ids: map an integer column, or an expression with {{\"sql\": ...}})")
    elif target == "timestamptz":
        if actual == "timestamp without time zone":
            rep.warn(where, f"{col} has no time zone: read in the time zone of the database session")
        elif actual == "date":
            rep.warn(where, f"{col} is a date: midnight, durations will be 0")
        elif actual in INT_TYPES | {"numeric", "real", "double precision"}:
            rep.error(where, f"{col} is {actual}: if it is an epoch, map "
                             f"{{\"sql\": \"to_timestamp(src.{qi(col)})\"}} (divide milliseconds by 1000)")
        elif actual != "timestamp with time zone":
            rep.warn(where, f"{col} is {actual}, cast to timestamptz: fails on values that are not dates")
    elif target == "exec_status":
        if actual == "boolean":
            rep.error(where, f"{col} is boolean: map an expression, e.g. "
                             f"{{\"sql\": \"CASE WHEN src.{qi(col)} THEN 'success' ELSE 'failed' END\"}}")
    elif target == "text" and actual not in TEXT_TYPES:
        rep.info(where, f"{col} is {actual}, shown as text")


def check_data(m, db, rep):
    cte = cte_sql(m)

    def q(sql):
        return db.query(cte + sql)

    try:
        counts = q("SELECT " + ", ".join(f"(SELECT count(*) FROM {t})" for t in ORDER))[0]
    except RuntimeError as e:
        rep.error("dry run", f"the views do not run on the database: {e}")
        return None
    for t, n in zip(ORDER, counts):
        rep.info(t, f"{n} rows")
        if num(n) == 0:
            rep.warn(t, "no rows")

    # status values of the real tables that the mapping does not translate
    for t in ORDER:
        raw = m.raw(t, "status")
        if raw is None:
            continue
        found = db.query(f"SELECT coalesce({raw}::text, '<NULL>'), count(*) FROM {m.from_sql(t)} src "
                         f"GROUP BY 1 ORDER BY 2 DESC LIMIT 50")
        known = ({v for vals in m.status_values.values() for v in vals} if m.status_values else set(STATUSES))
        unknown = [f"{v!r} ({n})" for v, n in found if v not in known]
        if unknown:
            rep.error(f"{t}.status", "values not translated (add them to status_values): " + ", ".join(unknown))
        else:
            rep.info(f"{t}.status", "values: " + ", ".join(f"{v} ({n})" for v, n in found))

    not_null = [(t, c) for t in ORDER for c in MODEL[t] if c not in ("actual_result", "kms_version_order")]
    sql = "SELECT " + ",\n".join(f"(SELECT count(*) FROM {t} WHERE {c} IS NULL)" for t, c in not_null)
    for (t, c), n in zip(not_null, q(sql)[0]):
        if num(n):
            level = rep.error if c == "id" or c.endswith("_id") else rep.warn
            level(f"{t}.{c}", f"{n} NULL value(s)")

    checks = [
        *[(t, "id", f"SELECT count(*) - count(DISTINCT id) FROM {t}", "duplicate id(s)", rep.error) for t in ORDER],
        *[(t, fk, f"SELECT count(*) FROM {t} x WHERE NOT EXISTS (SELECT 1 FROM {p} WHERE id = x.{fk})",
           f"row(s) whose {p} is missing (not shown)", rep.warn) for t, (p, fk) in PARENT.items()],
        ("step", "position", "SELECT count(*) - count(DISTINCT (test_id, position)) FROM step",
         "duplicate positions in a test: steps are matched by position", rep.warn),
        ("action", "position", "SELECT count(*) - count(DISTINCT (step_id, position)) FROM action",
         "duplicate positions in a step: actions are matched by position", rep.warn),
        ("test", "name", "SELECT count(*) - count(DISTINCT (campaign_id, name)) FROM test",
         "test names repeated in a campaign: runs are matched by name", rep.warn),
        ("test", "ended_at", "SELECT count(*) FROM test WHERE ended_at < started_at", "end before start", rep.warn),
        ("campaign", "ended_at", "SELECT count(*) FROM campaign WHERE ended_at < started_at",
         "end before start", rep.warn),
        ("test", "id", "SELECT count(*) FROM test t WHERE NOT EXISTS (SELECT 1 FROM step WHERE test_id = t.id)",
         "test(s) without steps", rep.warn),
        ("step", "id", "SELECT count(*) FROM step s WHERE NOT EXISTS (SELECT 1 FROM action WHERE step_id = s.id)",
         "step(s) without actions", rep.warn),
    ]
    for t, c, sql, msg, level in checks:
        n = num(q(sql)[0][0])
        if n:
            level(f"{t}.{c}", f"{n} {msg}")

    platforms = [r[0] for r in q("SELECT DISTINCT platform FROM campaign ORDER BY 1")]
    rep.info("campaign.platform", f"{len(platforms)} platform(s): " + ", ".join(platforms))
    if len(platforms) > 10:
        rep.warn("campaign.platform", "many platforms: check that it holds a platform name")
    values = {"platforms": platforms}
    for name, col in COMPONENTS:
        values[name] = [r[0] for r in q(f"SELECT DISTINCT {col} FROM campaign ORDER BY 1")]
    return values


# ---------------------------------------------------------------- dashboards
def adapt_dashboards(values, out):
    (out / "dashboards").mkdir(parents=True, exist_ok=True)
    for f in sorted(EXPORT.glob("*.json")):
        dash = json.loads(f.read_text())
        if values and dash.get("uid") == "kitchen-trends":
            patch_trends(dash, values)
        (out / "dashboards" / f.name).write_text(json.dumps(dash, indent=2, ensure_ascii=False) + "\n")
    if not values:
        print("WARN   dashboards: no database access, the Trends tooltip keeps the sample versions and platforms")


def patch_trends(dash, values):
    """Tooltip of Trends: its version fields are enums, which need the list of possible values."""
    names = [n for n, _ in COMPONENTS]
    enum = {n: values[n] for n in names}
    enum["changed"] = (["no change"] + [f"first campaign on {p}" for p in values["platforms"]]
                       + [", ".join(c) for k in range(1, len(names) + 1) for c in combinations(names, k)])
    for p in dash["panels"]:
        for tr in p.get("transformations", []):
            for conv in tr.get("options", {}).get("conversions", []):
                if conv.get("targetField") in enum:
                    conv["enumConfig"] = {"text": enum[conv["targetField"]]}
    for v in dash["templating"]["list"]:
        if v["name"] == "platform":
            v["current"] = {"text": values["platforms"], "value": values["platforms"]}


def template():
    """A mapping with every column of the model, mapped to the same name (edit the right-hand side)."""
    out = {"_doc": "Map each column of the dashboards' model to a column of your table (exact name), to "
                   "{\"sql\": \"expression using src.<column>\"} or to null (default / derived when allowed). "
                   "Remove status_values if your statuses already are success / skipped / failed.",
           "schema": "kitchen", "grafana_role": "grafana_ro",
           "status_values": {"success": ["success"], "skipped": ["skipped"], "failed": ["failed"]}}
    for table, cols in MODEL.items():
        out[table] = {"from": f"public.{table}", "columns": {c: c for c in cols},
                      "_columns": {c: f"{typ}, {'required' if d == REQUIRED else 'optional'}: {desc}"
                                   for c, (typ, d, desc) in cols.items()}}
    return json.dumps(out, indent=2) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("template", help="print a mapping file to fill in")
    for name, help_ in (("check", "check a mapping"), ("build", "check a mapping and generate the files")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("mapping", type=Path)
        p.add_argument("--psql", metavar="DSN", help="connection string for psql (read-only queries)")
        p.add_argument("--grafana", metavar="URL", help="query through this Grafana instead of psql")
        p.add_argument("--datasource", metavar="UID", help="uid of the PostgreSQL data source (with --grafana)")
        if name == "build":
            p.add_argument("--out", type=Path, required=True, help="output directory")
            p.add_argument("--force", action="store_true", help="generate even if the check finds errors")
    args = ap.parse_args()
    if args.cmd == "template":
        sys.stdout.write(template())
        return 0

    rep = Report()
    try:
        m = Mapping(json.loads(args.mapping.read_text()))
    except (OSError, json.JSONDecodeError) as e:
        print(f"{args.mapping}: {e}")
        return 1
    check_structure(m, rep)
    db = None
    if args.grafana:
        if not args.datasource:
            ap.error("--grafana needs --datasource")
        db = Grafana(args.grafana, args.datasource, os.environ.get("GRAFANA_TOKEN"))
    elif args.psql:
        db = Psql(args.psql)
    values = None
    if db:
        values = check_database(m, db, rep)
    elif not db:
        rep.warn("database", "no --psql / --grafana: names, types and data not checked")
    rep.print()

    if args.cmd == "build":
        if rep.errors and not args.force:
            print("nothing generated: fix the errors (or use --force)")
            return 1
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "install.sql").write_text(install_sql(m))
        adapt_dashboards(values, args.out)
        print(f"generated {args.out}/install.sql and {args.out}/dashboards/*.json")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
