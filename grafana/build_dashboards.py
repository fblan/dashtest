#!/usr/bin/env python3
"""Build the Grafana dashboard JSON files in grafana/dashboards/."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"      # provisioned into the local Grafana
EXPORT = Path(__file__).resolve().parent / "export"      # portable copies to paste elsewhere
DS = {"type": "grafana-postgresql-datasource", "uid": "kitchen-pg"}

STATUS_COLORS = {"success": "green", "skipped": "orange", "failed": "red"}
# platforms, as in db/init/01_schema.sql (default selection of the Trends platform filter)
PLATFORMS = {"IN": "InHouse kitchen", "OUT": "Outdoor kitchen"}
STATUS_MAPPING = [{"type": "value", "options": {
    s: {"color": c, "text": s, "index": i} for i, (s, c) in enumerate(STATUS_COLORS.items())}}]
TRANSITION_MAPPING = [{"type": "value", "options": {
    "regression": {"color": "red", "text": "▼ regression", "index": 0},
    "fixed": {"color": "green", "text": "▲ fixed", "index": 1},
    "still_failing": {"color": "dark-red", "text": "still failing", "index": 2},
    "stable_ok": {"color": "transparent", "text": "ok", "index": 3},
    "new": {"color": "blue", "text": "new", "index": 4},
    "skipped": {"color": "orange", "text": "skipped", "index": 5}}}]


def target(sql, fmt="table", ref="A"):
    return {"refId": ref, "datasource": DS, "rawQuery": True, "editorMode": "code", "format": fmt, "rawSql": sql}


class Layout:
    def __init__(self):
        self.panels, self.y, self.next_id = [], 0, 1

    def row(self, *panels_and_widths, h=8):
        x = 0
        for panel, w in panels_and_widths:
            panel["id"] = self.next_id
            panel["gridPos"] = {"x": x, "y": self.y, "w": w, "h": h}
            self.next_id += 1
            x += w
            self.panels.append(panel)
        self.y += h


def panel(ptype, title, sql, fmt="table", defaults=None, overrides=None, options=None, desc=""):
    return {"type": ptype, "title": title, "description": desc, "datasource": DS, "targets": [target(sql, fmt)],
            "fieldConfig": {"defaults": defaults or {}, "overrides": overrides or []}, "options": options or {}}


def override(field, *props, regex=False):
    return {"matcher": {"id": "byRegexp" if regex else "byName", "options": field},
            "properties": [{"id": k, "value": v} for k, v in props]}


def color_cell(field, mapping=STATUS_MAPPING):
    return override(field, ("mappings", mapping), ("custom.cellOptions", {"type": "color-background"}))


def test_link(field, id_field="id", compare="0"):
    """Clicking the cell opens the Test details dashboard for that test execution,
    compared with test run `compare` (0 = previous run of the test on the platform)."""
    return override(field, ("links", [{
        "title": "Open test details",
        "url": "/d/kitchen-test/test-details?var-test=${__data.fields.%s}&var-compare=%s&${__url_time_range}"
               % (id_field, compare)}]))


def wrap(*fields):
    """Show multi-line text (descriptions, expected/actual results) on several lines."""
    return override("^(" + "|".join(fields) + ")$", ("custom.wrapText", True), regex=True)


def width(field, w):
    return override(field, ("custom.width", w))


def fixed_color(field, color):
    return override(field, ("color", {"mode": "fixed", "fixedColor": color}))


def stat(title, sql, defaults=None, overrides=None, color_mode="background"):
    return panel("stat", title, sql, defaults=defaults, overrides=overrides, options={
        "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": "/.*/"},
        "colorMode": color_mode, "graphMode": "none", "textMode": "value_and_name", "justifyMode": "center"})


def table(title, sql, overrides=None, desc=""):
    return panel("table", title, sql, overrides=overrides, desc=desc,
                 defaults={"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True}},
                 options={"showHeader": True, "cellHeight": "sm"})


def query_var(name, label, sql, multi=False, include_all=False, refresh=1):
    return {"type": "query", "name": name, "label": label, "datasource": DS,
            "query": sql, "definition": sql, "refresh": refresh, "multi": multi,
            "includeAll": include_all, "sort": 0, "current": {}, "options": []}


def platform_var(include_all=True):
    """Platform filter shared by all dashboards (multi-select, all platforms by default). Without include_all
    there is no "All" option: the platforms are selected one by one (both by default)."""
    v = query_var("platform", "Platform",
                  "SELECT code || ' — ' || name AS __text, code AS __value FROM platform ORDER BY code",
                  multi=True, include_all=include_all)
    v["current"] = ({"text": ["All"], "value": ["$__all"]} if include_all else
                    {"text": [f"{code} — {name}" for code, name in PLATFORMS.items()], "value": list(PLATFORMS)})
    return v


def versions_stat(where):
    """The 5 software versions of a campaign, KMS (system under test) highlighted."""
    p = stat("Software versions",
             f"""SELECT kms_version AS "KMS (under test)", kes_version AS "KES", ktrs_version AS "KTRS",
       gateway_version AS "Gateway", testcampaign_version AS "TestCampaign"
FROM campaign WHERE {where}""",
             defaults={"color": {"mode": "fixed", "fixedColor": "transparent"}},
             overrides=[override("KMS (under test)", ("color", {"mode": "fixed", "fixedColor": "blue"}))])
    p["options"].update({"textMode": "value_and_name", "orientation": "vertical", "wideLayout": True,
                         "text": {"titleSize": 13, "valueSize": 24}})
    p["description"] = ("KMS = Kitchen Management System (system under test), KES = KitEquipmentSimulator, "
                        "KTRS = Kitchen Test Runner System, Gateway, TestCampaign = test definitions")
    return p


def changes_stat(where):
    """Versions changed since the previous campaign on the platform; orange when something changed,
    otherwise the date and content of the last change."""
    p = stat("Versions changed since the previous campaign on this platform",
             f"""SELECT CASE WHEN changes = 'no change'
            THEN 'None — last change ' || to_char(last_change_at, 'YYYY-MM-DD') || ': ' || last_change
            ELSE changes END AS changes
FROM v_campaign WHERE {where}""",
             defaults={"mappings": [
                 {"type": "regex", "options": {"pattern": "^None.*", "result": {"color": "transparent"}}},
                 {"type": "regex", "options": {"pattern": ".*", "result": {"color": "orange"}}}]})
    p["options"]["textMode"] = "value"
    return p


def release_annotations():
    kms = {
        "name": "KMS releases", "enable": True, "iconColor": "blue", "datasource": DS,
        "target": target("SELECT min(started_at) AS time, 'KMS ' || kms_version AS text, 'KMS' AS tags "
                         "FROM campaign WHERE platform IN ($platform) GROUP BY kms_version", ref="Anno")}
    parts = [f"SELECT min(started_at) AS time, '{label} ' || {col} AS text, '{label}' AS tags FROM campaign "
             f"WHERE platform IN ($platform) GROUP BY {col} "
             f"HAVING min(started_at) > (SELECT min(started_at) FROM campaign)"
             for label, col in (("KES", "kes_version"), ("KTRS", "ktrs_version"),
                                ("Gateway", "gateway_version"), ("TestCampaign", "testcampaign_version"))]
    others = {
        "name": "Other component releases (KES, KTRS, Gateway, TestCampaign)", "enable": True,
        "iconColor": "purple", "datasource": DS, "target": target("\nUNION ALL\n".join(parts), ref="Anno2")}
    return [kms, others]


def dashboard(uid, title, layout, variables, time=("now-7M", "now"), annotations=()):
    return {
        "uid": uid, "title": title, "tags": ["kitchen", "qa"], "editable": True, "schemaVersion": 39,
        "time": {"from": time[0], "to": time[1]}, "timezone": "browser", "refresh": "",
        "templating": {"list": variables},
        "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                  "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                  "name": "Annotations & Alerts", "type": "dashboard"}, *annotations]},
        "links": [{"title": "Kitchen QA", "type": "dashboards", "tags": ["kitchen"], "asDropdown": False,
                   "includeVars": False, "keepTime": True}],
        "panels": layout.panels,
    }


# ---------------------------------------------------------------- 01 Campaign overview
OVERVIEW_URL = ("/d/kitchen-overview/campaign-overview?var-campaign=${campaign}&${compare:queryparam}"
                "&${platform:queryparam}&${__url_time_range}")
# campaign compared with: the one picked in "Compare with", or (0) the previous campaign on the platform
BASE = "coalesce(nullif($compare, 0), (SELECT prev_campaign_id FROM v_campaign WHERE id = $campaign))"
# per test of the campaign: d = test_diff with the same test (by name) in the compared campaign
DIFF_JOIN = f"""LEFT JOIN test bt ON bt.campaign_id = {BASE} AND bt.name = t.name
LEFT JOIN LATERAL test_diff(t.id, bt.id) d ON true"""


# Filters of a list (tests of the overview, actions of the step details), set by clicking tiles. Each click sets
# one filter and clears the other: hidden variables <status_var> (multi, All) and "change" (all / regression / fix).
def status_filter(field, color, value, url=OVERVIEW_URL, status_var="test_status", items="tests"):
    """Tile of a status count stat: fixed color, click = filter the list on that status
    and clear the regression / fix filter, so the list matches the number on the tile."""
    return override(field, ("color", {"mode": "fixed", "fixedColor": color}), ("links", [{
        "title": f"Show {field} {items}",
        "url": url + f"&var-{status_var}=" + value + "&var-change=all"}]))


def status_filters(**kw):
    """The all / success / skipped / failed tiles of a status count stat."""
    return [status_filter("all", "blue", "%24__all", **kw), *[status_filter(st, c, st, **kw)
                                                             for st, c in STATUS_COLORS.items()]]


def change_filter(field, color, value, title, url=OVERVIEW_URL, status_var="test_status"):
    """Comparison tile: colored when > 0, click = list only those items (and clear the status filter)."""
    return override(field, ("color", {"mode": "thresholds"}), ("thresholds", {"mode": "absolute", "steps": [
        {"color": "text", "value": None}, {"color": color, "value": 1}]}), ("links", [{
            "title": title, "url": url + f"&var-{status_var}=%24__all&var-change=" + value}]))


def filter_vars(status_var, items):
    """Hidden variables behind status_filter / change_filter; the tiles act as the filter buttons."""
    changes = [("all", f"all {items}"), ("regression", "with regressions"), ("fix", "with fixes")]
    return [
        {"type": "custom", "name": status_var, "label": "Status", "hide": 2,
         "query": "success,skipped,failed", "multi": True, "includeAll": True,
         "current": {"text": ["All"], "value": ["$__all"]},
         "options": [{"text": "All", "value": "$__all", "selected": True}]
                    + [{"text": v, "value": v, "selected": False} for v in STATUS_COLORS]},
        {"type": "custom", "name": "change", "label": "Change vs compared run", "hide": 2,
         "query": ", ".join(f"{text} : {value}" for value, text in changes),
         "current": {"text": changes[0][1], "value": changes[0][0]},
         "options": [{"text": text, "value": value, "selected": value == "all"} for value, text in changes]},
    ]


def comparison_row():
    """Tiles comparing the campaign with the campaign picked in "Compare with" (default: previous on the platform)."""
    previous = stat(
        "Compared with (click to open it)",
        """SELECT coalesce(p.label || '  (' || p.status || ')', 'none: first campaign on this platform') AS compared,
       p.id AS base_id
FROM (SELECT %s AS id) b LEFT JOIN v_campaign p ON p.id = b.id""" % BASE, color_mode="none")
    previous["options"]["reduceOptions"]["fields"] = "/^compared$/"
    previous["options"].update({"textMode": "value", "text": {"valueSize": 20}})
    previous["fieldConfig"]["defaults"]["links"] = [{
        "title": "Open the compared campaign",
        "url": "/d/kitchen-overview/campaign-overview?var-campaign=${__data.fields.base_id}&var-compare=0"
               "&${platform:queryparam}&${__url_time_range}"}]

    pass_rate = stat(
        "Pass rate: compared campaign / change",
        """SELECT p.pass_rate AS compared,
       CASE WHEN c.pass_rate = p.pass_rate THEN '±0.0 pts'
            ELSE to_char(c.pass_rate - p.pass_rate, 'FMSG990.0') || ' pts' END AS change
FROM v_campaign_summary c
JOIN v_campaign_summary p ON p.campaign_id = %s
WHERE c.campaign_id = $campaign""" % BASE,
        overrides=[override("compared", ("unit", "percent"), ("color", {"mode": "fixed", "fixedColor": "text"})),
                   override("change", ("mappings", [
                       {"type": "regex", "options": {"pattern": "^\\+.*", "result": {"color": "green"}}},
                       {"type": "regex", "options": {"pattern": "^-.*", "result": {"color": "red"}}},
                       {"type": "regex", "options": {"pattern": ".*", "result": {"color": "text"}}}]))],
        color_mode="value")

    transitions = stat(
        "Tests with regressions / fixes (click to list them)",
        """SELECT count(*) FILTER (WHERE d.actions_regressed > 0) AS regressions,
       count(*) FILTER (WHERE d.actions_fixed > 0) AS fixes
FROM test t %s
WHERE t.campaign_id = $campaign""" % DIFF_JOIN,
        overrides=[change_filter("regressions", "red", "regression", "Show only the tests with regressions"),
                   change_filter("fixes", "green", "fix", "Show only the tests with fixes")],
        color_mode="value")
    transitions["description"] = (
        "Compared action by action with the same test in the compared campaign (matched by step and "
        "action position); skipped and failed count as not passing. Regression: at least one action passed before "
        "and does not pass now. Fix: at least one action did not pass before and passes now.")

    return [(previous, 9), (pass_rate, 6), (transitions, 9)]


def campaign_overview():
    L = Layout()
    L.row(
        (stat("Campaign status", "SELECT status::text AS status FROM campaign WHERE id = $campaign",
              defaults={"mappings": STATUS_MAPPING}), 4),
        (stat("Platform", "SELECT p.code || ' · ' || p.name AS platform FROM campaign c "
                          "JOIN platform p ON p.code = c.platform WHERE c.id = $campaign",
              defaults={"mappings": [{"type": "regex", "options": {"pattern": "^(IN|OUT).*", "result": {
                  "color": "purple"}}}]}, color_mode="none"), 4),
        (stat("Pass rate", "SELECT pass_rate AS \"pass rate\" FROM v_campaign_summary WHERE campaign_id = $campaign",
              defaults={"unit": "percent", "min": 0, "max": 100, "color": {"mode": "thresholds"},
                        "thresholds": {"mode": "absolute", "steps": [
                            {"color": "red", "value": None}, {"color": "orange", "value": 80},
                            {"color": "green", "value": 100}]}}), 3),
        (stat("Tests (click to filter the list)",
              "SELECT n_tests AS all, n_success AS success, n_skipped AS skipped, n_failed AS failed "
              "FROM v_campaign_summary WHERE campaign_id = $campaign",
              overrides=status_filters()), 10),
        (stat("Duration", "SELECT duration_s AS duration FROM v_campaign_summary WHERE campaign_id = $campaign",
              defaults={"unit": "s"}, color_mode="none"), 3),
        h=4)
    L.row((versions_stat("id = $campaign"), 15), (changes_stat("id = $campaign"), 9), h=4)
    L.row(({"type": "row", "title": "Comparison with: ${compare:text}",
            "collapsed": False, "panels": []}, 24), h=1)
    L.row(*comparison_row(), h=4)
    L.row((table(
        "Tests: ${test_status:text}, ${change:text} (click a name to drill down)",
        """SELECT t.id, CASE WHEN $compare = 0 THEN 0 ELSE coalesce(bt.id, 0) END AS compare_test,
       t.name, t.status::text AS status,
       (SELECT count(*) FROM step WHERE test_id = t.id) AS steps,
       d.steps_regressed AS "steps with regressions", d.steps_fixed AS "steps with fixes",
       extract(epoch FROM t.ended_at - t.started_at) / 60 AS duration
FROM test t %s
WHERE t.campaign_id = $campaign AND t.status::text IN ($test_status)
  AND CASE '$change' WHEN 'regression' THEN d.actions_regressed > 0
                     WHEN 'fix'        THEN d.actions_fixed > 0
                     ELSE true END
ORDER BY t.rank""" % DIFF_JOIN,
        desc="steps with regressions / fixes: steps containing at least one action that passed in the compared "
             "campaign and does not pass now / did not pass and passes now. "
             "Empty = no compared campaign, or the test is not in it.",
        overrides=[color_cell("status"), override("^(id|compare_test)$", ("custom.hidden", True), regex=True),
                   test_link("name", compare="${__data.fields.compare_test}"),
                   width("status", 100), override("steps", ("custom.width", 80), ("custom.align", "center")),
                   override("duration", ("unit", "m"), ("decimals", 1), ("custom.width", 100)), *[
                       override(f"steps with {kind}", ("custom.width", 170), ("custom.align", "center"),
                                ("custom.filterable", False), ("custom.cellOptions", {"type": "color-background"}),
                                ("color", {"mode": "thresholds"}),
                                ("thresholds", {"mode": "absolute", "steps": [
                                    {"color": "transparent", "value": None}, {"color": color, "value": 1}]}))
                       for kind, color in (("regressions", "red"), ("fixes", "green"))]]),
           24), h=17)

    variables = [
        platform_var(),
        query_var("campaign", "Campaign",
                  "SELECT label || '  (' || status || ')' AS __text, id AS __value FROM v_campaign "
                  "WHERE platform IN ($platform) ORDER BY started_at DESC"),
        # 0 = previous campaign on the platform (follows the selected campaign), else a campaign id
        query_var("compare", "Compare with", """SELECT __text, __value FROM (
    SELECT 0 AS ord, NULL::timestamptz AS started_at, 0 AS __value,
           'previous on this platform: ' || coalesce(p.label || '  (' || p.status || ')', 'none') AS __text
    FROM v_campaign c LEFT JOIN v_campaign p ON p.id = c.prev_campaign_id WHERE c.id = $campaign
    UNION ALL
    SELECT 1, o.started_at, o.id, o.label || '  (' || o.status || ')'
    FROM v_campaign c JOIN v_campaign o ON o.platform = c.platform AND o.id <> c.id WHERE c.id = $campaign
) x ORDER BY ord, started_at DESC"""),
        *filter_vars("test_status", "tests"),
    ]
    return dashboard("kitchen-overview", "Campaign overview", L, variables)


# ---------------------------------------------------------------- 02 Trends
def trends():
    """One column per selected platform: test results per campaign, and under it the tests with regressions /
    fixes compared with the previous campaign on the platform."""
    L = Layout()
    line = {"drawStyle": "line", "lineWidth": 2, "showPoints": "always", "pointSize": 8, "fillOpacity": 0,
            "spanNulls": True}
    legend = {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
              "tooltip": {"mode": "multi", "sort": "none"}}
    total = override("total", ("color", {"mode": "fixed", "fixedColor": "text"}),
                     ("custom.lineStyle", {"fill": "dash", "dash": [10, 10]}))

    def per_platform(p):
        p.update({"repeat": "platform", "repeatDirection": "h", "maxPerRow": 2})
        return p
    L.row((per_platform(panel(
        "timeseries", "Test results per campaign — $platform",
        """SELECT started_at AS time, n_tests AS total, n_success AS success, n_skipped AS skipped,
       n_failed AS failed
FROM v_campaign_summary
WHERE $__timeFilter(started_at) AND platform IN (${platform:sqlstring})
ORDER BY 1""",
        defaults={"unit": "none", "decimals": 0, "min": 0, "custom": line},
        overrides=[total, *[fixed_color(st, c) for st, c in STATUS_COLORS.items()]],
        options=legend,
        desc="Number of tests of each campaign on the platform, by status.")), 24), h=10)
    L.row((per_platform(panel(
        "timeseries", "Tests with regressions / fixes per campaign — $platform",
        """SELECT c.started_at AS time, count(*) AS total,
       count(*) FILTER (WHERE d.actions_regressed > 0) AS "with regressions",
       count(*) FILTER (WHERE d.actions_fixed > 0) AS "with fixes"
FROM campaign c
JOIN test t ON t.campaign_id = c.id
LEFT JOIN v_test_diff d ON d.test_id = t.id
WHERE $__timeFilter(c.started_at) AND c.platform IN (${platform:sqlstring})
GROUP BY c.id ORDER BY 1""",
        defaults={"unit": "none", "decimals": 0, "min": 0, "custom": line},
        overrides=[total, fixed_color("with regressions", "red"), fixed_color("with fixes", "green")],
        options=legend,
        desc="Compared with the previous campaign on the platform, action by action (skipped and failed count as "
             "not passing). With regressions: at least one action passed before and does not pass now. "
             "With fixes: at least one action did not pass before and passes now.")), 24), h=10)
    return dashboard("kitchen-trends", "Trends over time", L, [platform_var(include_all=False)],
                     annotations=release_annotations())


# ---------------------------------------------------------------- 04 Test details
# Step details link: test id, step id, compared step (0 = same step in the previous run of the test)
STEP_URL = ("/d/kitchen-step/step-details?var-test=%s&var-step=%s&var-compare=%s"
            "&${platform:queryparam}&${__url_time_range}")
# the current step, for the filter tiles of Step details (they add the filter variables)
STEP_SELF = STEP_URL % ("${test}", "${step}", "${compare}")
# links opening another step reset the filters
STEP_URL += "&var-action_status=%%24__all&var-change=all"
# test run compared with: the one picked in "Compare with", or (0) the previous run of the test on the platform
BASE_TEST = "coalesce(nullif($compare, 0), (SELECT prev_test_id FROM v_test_result WHERE test_id = $test))"


def test_details():
    L = Layout()
    head = """SELECT t.name, t.status::text AS status, c.platform || ' · ' || p.name AS platform,
       to_char(c.started_at, 'YYYY-MM-DD HH24:MI') AS campaign, c.id AS campaign_id,
       extract(epoch FROM t.ended_at - t.started_at) AS duration
FROM test t JOIN campaign c ON c.id = t.campaign_id JOIN platform p ON p.code = c.platform
WHERE t.id = $test"""

    def head_stat(title, field, **kw):
        p = stat(title, head, **kw)
        p["options"]["reduceOptions"]["fields"] = f"/^{field}$/"
        p["options"]["textMode"] = "value"
        return p

    campaign_stat = head_stat("Campaign (click to open)", "campaign", color_mode="none")
    campaign_stat["fieldConfig"]["defaults"]["links"] = [{
        "title": "Open campaign overview",
        "url": "/d/kitchen-overview/campaign-overview?var-campaign=${__data.fields.campaign_id}"}]
    L.row(
        (head_stat("Test", "name", color_mode="none"), 7),
        (head_stat("Status", "status", defaults={"mappings": STATUS_MAPPING}), 3),
        (head_stat("Platform", "platform", color_mode="none"), 4),
        (campaign_stat, 6),
        (head_stat("Duration", "duration", defaults={"unit": "s"}, color_mode="none"), 4),
        h=4)
    in_campaign = "id = (SELECT campaign_id FROM test WHERE id = $test)"
    L.row((versions_stat(in_campaign), 15), (changes_stat(in_campaign), 9), h=4)

    compared = stat(
        "Compared with (click to open it)",
        """SELECT coalesce(c.label || '  (' || r.status || ')', 'none: first run of this test on this platform')
           AS compared, r.test_id AS base_id
FROM (SELECT %s AS id) b
LEFT JOIN v_test_result r ON r.test_id = b.id LEFT JOIN v_campaign c ON c.id = r.campaign_id""" % BASE_TEST,
        color_mode="none")
    compared["options"]["reduceOptions"]["fields"] = "/^compared$/"
    compared["options"].update({"textMode": "value", "text": {"valueSize": 20}})
    compared["fieldConfig"]["defaults"]["links"] = [{
        "title": "Open the compared run",
        "url": "/d/kitchen-test/test-details?var-test=${__data.fields.base_id}&var-compare=0"
               "&${platform:queryparam}&${__url_time_range}"}]
    totals = stat(
        "Actions with regressions / fixes",
        """SELECT d.actions_regressed AS regressions, d.actions_fixed AS fixes
FROM test_diff($test, %s) d""" % BASE_TEST,
        overrides=[override(field, ("color", {"mode": "thresholds"}), ("thresholds", {"mode": "absolute", "steps": [
            {"color": "text", "value": None}, {"color": color, "value": 1}]}))
            for field, color in (("regressions", "red"), ("fixes", "green"))],
        color_mode="value")
    totals["fieldConfig"]["defaults"]["noValue"] = "no compared run"
    totals["description"] = (
        "Compared action by action with the compared run (matched by step and action position); skipped and "
        "failed count as not passing. Regression: the action passed before and does not pass now. "
        "Fix: it did not pass before and passes now.")
    L.row(({"type": "row", "title": "Comparison with: ${compare:text}", "collapsed": False, "panels": []}, 24), h=1)
    L.row((compared, 14), (totals, 10), h=4)
    L.row((table(
        "Steps (click a step to open it)",
        """SELECT s.id AS step_id,
       CASE WHEN $compare = 0 THEN 0
            ELSE coalesce((SELECT id FROM step WHERE test_id = b.id AND position = s.position), 0) END AS compare_step,
       s.position AS "#", s.description, s.expected_result AS expected, s.actual_result AS actual,
       s.status::text AS status, d.actions,
       CASE WHEN b.id IS NOT NULL THEN d.actions_regressed END AS "actions with regressions",
       CASE WHEN b.id IS NOT NULL THEN d.actions_fixed END AS "actions with fixes"
FROM (SELECT %s AS id) b
CROSS JOIN step_diff($test, b.id) d
JOIN step s ON s.id = d.step_id
ORDER BY s.position""" % BASE_TEST,
        desc="actions with regressions / fixes: actions of the step that passed in the compared run and do not "
             "pass now / did not pass and pass now. Empty = no compared run.",
        overrides=[color_cell("status"), width("#", 50), width("status", 90), wrap("description", "expected", "actual"),
                   override("^(step_id|compare_step)$", ("custom.hidden", True), regex=True),
                   override("^(#|description)$", ("links", [{
                       "title": "Open step details",
                       "url": STEP_URL % ("${test}", "${__data.fields.step_id}", "${__data.fields.compare_step}")}]),
                            regex=True),
                   override("actions", ("custom.width", 80), ("custom.align", "center")), *[
                       override(f"actions with {kind}", ("custom.width", 170), ("custom.align", "center"),
                                ("custom.filterable", False), ("custom.cellOptions", {"type": "color-background"}),
                                ("color", {"mode": "thresholds"}),
                                ("thresholds", {"mode": "absolute", "steps": [
                                    {"color": "transparent", "value": None}, {"color": color, "value": 1}]}))
                       for kind, color in (("regressions", "red"), ("fixes", "green"))]]), 24), h=14)
    L.row((table(
        "History of this test on the same platform (click a date to open that run)",
        """SELECT r.test_id AS id, to_char(r.campaign_started_at, 'YYYY-MM-DD HH24:MI') AS date,
       r.kms_version AS "KMS", r.status::text AS status, r.transition,
       r.changes AS "changed since previous run", r.duration_s / 60.0 AS duration,
       f.step_position || '.' || f.action_position AS "stopped at", split_part(f.action_description, E'\\n', 1) AS action,
       f.expected_result AS expected, f.actual_result AS actual
FROM v_test_result r LEFT JOIN v_first_failure f ON f.test_id = r.test_id
WHERE (r.name, r.platform) = (SELECT t.name, c.platform FROM test t JOIN campaign c ON c.id = t.campaign_id
                              WHERE t.id = $test)
ORDER BY r.campaign_started_at DESC""",
        overrides=[color_cell("status"), color_cell("transition", TRANSITION_MAPPING),
                   override("id", ("custom.hidden", True)), width("date", 150), width("KMS", 70),
                   width("status", 85), width("transition", 115), width("stopped at", 90),
                   override("duration", ("unit", "m"), ("decimals", 1), ("custom.width", 90)),
                   test_link("date"), wrap("changed since previous run", "action", "expected", "actual")]),
           24), h=16)

    variables = [platform_var(), query_var(
        "test", "Test run",
        "SELECT t.name || '  —  ' || c.label || '  (' || t.status || ')' AS __text, t.id AS __value "
        "FROM test t JOIN v_campaign c ON c.id = t.campaign_id WHERE c.platform IN ($platform) "
        "ORDER BY c.started_at DESC, t.rank"),
        # 0 = previous run of the test on the platform (follows the selected run), else another run of the test
        query_var("compare", "Compare with", """SELECT __text, __value FROM (
    SELECT 0 AS ord, NULL::timestamptz AS started_at, 0 AS __value,
           'previous run on this platform: ' || coalesce(pc.label || '  (' || p.status || ')', 'none') AS __text
    FROM v_test_result r LEFT JOIN v_test_result p ON p.test_id = r.prev_test_id
    LEFT JOIN v_campaign pc ON pc.id = p.campaign_id WHERE r.test_id = $test
    UNION ALL
    SELECT 1, o.campaign_started_at, o.test_id, oc.label || '  (' || o.status || ')'
    FROM v_test_result r JOIN v_test_result o ON o.name = r.name AND o.platform = r.platform AND o.test_id <> r.test_id
    JOIN v_campaign oc ON oc.id = o.campaign_id WHERE r.test_id = $test
) x ORDER BY ord, started_at DESC""")]
    return dashboard("kitchen-test", "Test details", L, variables)


# step compared with: the one picked in "Compare with", or (0) the same step in the previous run of the test
BASE_STEP = """coalesce(nullif($compare, 0), (SELECT ps.id FROM step s JOIN v_test_result r ON r.test_id = s.test_id
    JOIN step ps ON ps.test_id = r.prev_test_id AND ps.position = s.position WHERE s.id = $step))"""


def step_details():
    L = Layout()
    head = """SELECT t.name, t.id AS test_id, s.position AS step, s.status::text AS status,
       c.platform || ' · ' || p.name AS platform,
       to_char(c.started_at, 'YYYY-MM-DD HH24:MI') AS campaign, c.id AS campaign_id,
       (SELECT sum(duration_ms) / 1000.0 FROM action WHERE step_id = s.id) AS duration
FROM step s JOIN test t ON t.id = s.test_id JOIN campaign c ON c.id = t.campaign_id
JOIN platform p ON p.code = c.platform
WHERE s.id = $step"""

    def head_stat(title, field, **kw):
        p = stat(title, head, **kw)
        p["options"]["reduceOptions"]["fields"] = f"/^{field}$/"
        p["options"]["textMode"] = "value"
        return p

    test_stat = head_stat("Test (click to open)", "name", color_mode="none")
    test_stat["fieldConfig"]["defaults"]["links"] = [{
        "title": "Open test details",
        "url": "/d/kitchen-test/test-details?var-test=${__data.fields.test_id}&var-compare=0"
               "&${platform:queryparam}&${__url_time_range}"}]
    campaign_stat = head_stat("Campaign (click to open)", "campaign", color_mode="none")
    campaign_stat["fieldConfig"]["defaults"]["links"] = [{
        "title": "Open campaign overview",
        "url": "/d/kitchen-overview/campaign-overview?var-campaign=${__data.fields.campaign_id}"}]
    L.row(
        (test_stat, 6),
        (head_stat("Step", "step", color_mode="none"), 2),
        (head_stat("Status", "status", defaults={"mappings": STATUS_MAPPING}), 3),
        (head_stat("Platform", "platform", color_mode="none"), 4),
        (campaign_stat, 5),
        (head_stat("Duration", "duration", defaults={"unit": "s"}, color_mode="none"), 4),
        h=4)
    in_campaign = "id = (SELECT t.campaign_id FROM step s JOIN test t ON t.id = s.test_id WHERE s.id = $step)"
    L.row((versions_stat(in_campaign), 15), (changes_stat(in_campaign), 9), h=4)
    L.row((table(
        "Step",
        """SELECT description, expected_result AS expected, actual_result AS actual FROM step WHERE id = $step""",
        overrides=[wrap("description", "expected", "actual")]), 15),
        (stat("Actions (click to filter the list)",
              """SELECT count(*) AS all, count(*) FILTER (WHERE status = 'success') AS success,
       count(*) FILTER (WHERE status = 'skipped') AS skipped, count(*) FILTER (WHERE status = 'failed') AS failed
FROM action WHERE step_id = $step""",
              overrides=status_filters(url=STEP_SELF, status_var="action_status", items="actions")), 9), h=6)

    compared = stat(
        "Compared with (click to open it)",
        """SELECT coalesce(c.label || '  (step ' || ps.status || ')', 'none: first run of this test on this platform')
           AS compared, ps.test_id AS base_test, ps.id AS base_step
FROM (SELECT %s AS id) b
LEFT JOIN step ps ON ps.id = b.id LEFT JOIN test pt ON pt.id = ps.test_id
LEFT JOIN v_campaign c ON c.id = pt.campaign_id""" % BASE_STEP,
        color_mode="none")
    compared["options"]["reduceOptions"]["fields"] = "/^compared$/"
    compared["options"].update({"textMode": "value", "text": {"valueSize": 20}})
    compared["fieldConfig"]["defaults"]["links"] = [{
        "title": "Open the compared step",
        "url": STEP_URL % ("${__data.fields.base_test}", "${__data.fields.base_step}", "0")}]
    totals = stat(
        "Actions with regressions / fixes (click to list them)",
        """SELECT d.actions_regressed AS regressions, d.actions_fixed AS fixes
FROM (SELECT %s AS id) b
JOIN step bs ON bs.id = b.id
CROSS JOIN step_diff((SELECT test_id FROM step WHERE id = $step), bs.test_id) d
WHERE d.step_id = $step""" % BASE_STEP,
        overrides=[change_filter("regressions", "red", "regression", "Show only the actions with regressions",
                                 url=STEP_SELF, status_var="action_status"),
                   change_filter("fixes", "green", "fix", "Show only the actions with fixes",
                                 url=STEP_SELF, status_var="action_status")],
        color_mode="value")
    totals["fieldConfig"]["defaults"]["noValue"] = "no compared run"
    totals["description"] = (
        "Compared action by action with the same step in the compared run (matched by action position); skipped "
        "and failed count as not passing. Regression: the action passed before and does not pass now. "
        "Fix: it did not pass before and passes now.")
    L.row(({"type": "row", "title": "Comparison with: ${compare:text}", "collapsed": False, "panels": []}, 24), h=1)
    L.row((compared, 14), (totals, 10), h=4)
    L.row((table(
        "Actions: ${action_status:text}, ${change:text}",
        """SELECT a.position AS "#", a.description, status_change(a.status, pa.status) AS evolution,
       a.status::text AS status, a.expected_result AS expected, a.actual_result AS actual,
       pa.status::text AS "compared status", pa.expected_result AS "compared expected", pa.actual_result AS "compared actual"
FROM action a
LEFT JOIN action pa ON pa.step_id = %s AND pa.position = a.position
WHERE a.step_id = $step AND a.status::text IN ($action_status)
  AND CASE '$change' WHEN 'regression' THEN status_change(a.status, pa.status) = 'regression'
                     WHEN 'fix'        THEN status_change(a.status, pa.status) = 'fixed'
                     ELSE true END
ORDER BY a.position""" % BASE_STEP,
        desc="evolution, then current run: status, expected, actual, then compared run (same action): status, "
             "expected, actual. evolution: regression = passed in the compared run and does not pass now (skipped / failed), "
             "fixed = the reverse.",
        overrides=[color_cell("status"), color_cell("compared status"), color_cell("evolution", TRANSITION_MAPPING),
                   width("#", 50), width("status", 90), width("compared status", 140), width("evolution", 115),
                   wrap("description", "expected", "actual", "compared expected", "compared actual")]), 24), h=14)
    L.row((table(
        "History of this step on the same platform (click a date to open that run)",
        """SELECT r.test_id, s.id AS step_id, to_char(r.campaign_started_at, 'YYYY-MM-DD HH24:MI') AS date,
       r.kms_version AS "KMS", s.status::text AS status, s.actual_result AS actual
FROM step cur
JOIN test ct ON ct.id = cur.test_id
JOIN v_test_result cr ON cr.test_id = ct.id
JOIN v_test_result r ON r.name = cr.name AND r.platform = cr.platform
JOIN step s ON s.test_id = r.test_id AND s.position = cur.position
WHERE cur.id = $step
ORDER BY r.campaign_started_at DESC""",
        overrides=[color_cell("status"), override("^(test_id|step_id)$", ("custom.hidden", True), regex=True),
                   width("date", 150), width("KMS", 70), width("status", 90), wrap("actual"),
                   override("date", ("links", [{
                       "title": "Open this step in that run",
                       "url": STEP_URL % ("${__data.fields.test_id}", "${__data.fields.step_id}", "0")}]))]),
           24), h=16)

    variables = [platform_var(), query_var(
        "test", "Test run",
        "SELECT t.name || '  —  ' || c.label || '  (' || t.status || ')' AS __text, t.id AS __value "
        "FROM test t JOIN v_campaign c ON c.id = t.campaign_id WHERE c.platform IN ($platform) "
        "ORDER BY c.started_at DESC, t.rank"),
        query_var("step", "Step",
                  "SELECT position || '. ' || split_part(description, E'\\n', 1) || '  (' || status || ')' AS __text, "
                  "id AS __value FROM step WHERE test_id = $test ORDER BY position"),
        # 0 = same step in the previous run of the test on the platform, else the same step in another run
        query_var("compare", "Compare with", """SELECT __text, __value FROM (
    SELECT 0 AS ord, NULL::timestamptz AS started_at, 0 AS __value,
           'previous run on this platform: ' || coalesce(pc.label || '  (step ' || ps.status || ')', 'none') AS __text
    FROM step s JOIN v_test_result r ON r.test_id = s.test_id
    LEFT JOIN v_test_result p ON p.test_id = r.prev_test_id
    LEFT JOIN step ps ON ps.test_id = p.test_id AND ps.position = s.position
    LEFT JOIN v_campaign pc ON pc.id = p.campaign_id WHERE s.id = $step
    UNION ALL
    SELECT 1, o.campaign_started_at, os.id, oc.label || '  (step ' || os.status || ')'
    FROM step s JOIN v_test_result r ON r.test_id = s.test_id
    JOIN v_test_result o ON o.name = r.name AND o.platform = r.platform AND o.test_id <> r.test_id
    JOIN step os ON os.test_id = o.test_id AND os.position = s.position
    JOIN v_campaign oc ON oc.id = o.campaign_id WHERE s.id = $step
) x ORDER BY ord, started_at DESC"""),
        *filter_vars("action_status", "actions")]
    return dashboard("kitchen-step", "Step details", L, variables)


def portable(dash):
    """Copy of a dashboard for pasting into another Grafana (Dashboards > New > Import).

    The data source uid is replaced by an input, so the import screen asks which
    PostgreSQL data source to use. Dashboard uids are kept: cross-dashboard links rely on them.
    """
    text = json.dumps(dash, ensure_ascii=False).replace(f'"uid": "{DS["uid"]}"', '"uid": "${DS_KITCHEN}"')
    out = {"__inputs": [{"name": "DS_KITCHEN", "label": "Kitchen PostgreSQL", "description": "",
                         "type": "datasource", "pluginId": DS["type"], "pluginName": "PostgreSQL"}],
           "__requires": [{"type": "datasource", "id": DS["type"], "name": "PostgreSQL", "version": "1.0.0"},
                          {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "12.0.0"}]}
    out.update(json.loads(text))
    return out


def main():
    OUT.mkdir(exist_ok=True)
    EXPORT.mkdir(exist_ok=True)
    for fname, build in [("01_campaign_overview.json", campaign_overview),
                         ("02_trends.json", trends),
                         ("04_test_details.json", test_details),
                         ("05_step_details.json", step_details)]:
        dash = build()
        (OUT / fname).write_text(json.dumps(dash, indent=2, ensure_ascii=False) + "\n")
        (EXPORT / fname).write_text(json.dumps(portable(dash), indent=2, ensure_ascii=False) + "\n")
        print(OUT / fname, "+ export")


if __name__ == "__main__":
    main()
