#!/usr/bin/env python3
"""Run every SQL query of the dashboards (variables, annotations, panels) through a Grafana's
/api/ds/query and print ok / error per query. Works with anonymous access (no login).

usage: python3 tools/check_panels.py [GRAFANA_URL]   (default http://localhost:3000)
"""
import json, sys, urllib.error, urllib.request
from pathlib import Path

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000").rstrip("/")
DASHBOARDS = Path(__file__).resolve().parent.parent / "grafana" / "dashboards"

# Replacements for the Grafana variables. Order matters: longer names first ($tests before $test).
VARS = {
    "${platform:sqlstring}": "'IN','OUT'", "${platform:queryparam}": "var-platform=IN", "$platform": "'IN','OUT'",
    "$test_status": "'success','skipped','failed'",
    "$tests": "'03_oven_beef_wellington','10_oven_souffle','06_fryer_tempura','04_sous_vide_duck_breast'",
    "'$change'": "'regression'", "$compare": "0",
    "${campaign}": "16", "$campaign": "16", "$test": "220", "'$base'": "'1.2.0'", "'$target'": "'1.3.0'",
}


def query(sql, fmt):
    for k, v in VARS.items():
        sql = sql.replace(k, v)
    body = {"from": "now-1y", "to": "now", "queries": [{"refId": "A", "datasource": {"uid": "kitchen-pg"},
            "rawSql": sql, "format": fmt, "rawQuery": True, "editorMode": "code"}]}
    req = urllib.request.Request(URL + "/api/ds/query", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req))["results"]["A"]
    except urllib.error.HTTPError as e:
        return "HTTP %s %s" % (e.code, e.read()[:300])
    if "error" in r:
        return "ERROR " + r["error"]
    frames = r.get("frames", [])
    rows = sum(len(f["data"]["values"][0]) if f["data"].get("values") else 0 for f in frames)
    return f"ok frames={len(frames)} rows={rows}"


for f in sorted(DASHBOARDS.glob("*.json")):
    d = json.load(open(f))
    print("==", d["title"])
    for v in d["templating"]["list"]:
        if v["type"] == "query":  # custom variables are not SQL
            print("  var", v["name"], query(v["query"], "table"))
    for a in d["annotations"]["list"][1:]:
        print("  anno", query(a["target"]["rawSql"], "table"))
    for p in d["panels"]:
        if "targets" in p:  # row panels have no query
            t = p["targets"][0]
            print("  ", p["title"][:55].ljust(55), query(t["rawSql"], t["format"]))
