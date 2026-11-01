#!/usr/bin/env python3
"""Build the Grafana dashboard JSON files in grafana/dashboards/."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"      # provisioned into the local Grafana
EXPORT = Path(__file__).resolve().parent / "export"      # portable copies to paste elsewhere
DS = {"type": "grafana-postgresql-datasource", "uid": "kitchen-pg"}

STATUS_COLORS = {"success": "green", "skipped": "orange", "failed": "red"}
STATUS_MAPPING = [{"type": "value", "options": {
    s: {"color": c, "text": s, "index": i} for i, (s, c) in enumerate(STATUS_COLORS.items())}}]
STATUS_NUM_MAPPING = [{"type": "value", "options": {
    str(i): {"color": c, "text": s, "index": i} for i, (s, c) in enumerate(STATUS_COLORS.items())}}]
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


def test_link(field, id_field="id"):
    """Clicking the cell opens the Test details dashboard for that test execution."""
    return override(field, ("links", [{
        "title": "Open test details",
        "url": "/d/kitchen-test/test-details?var-test=${__data.fields.%s}&${__url_time_range}" % id_field}]))


def count_cells():
    """Step/action counters: narrow, colored background (white text) only when > 0; zeros stay plain."""
    return [override(f"(steps|actions) {suffix}",
                     ("custom.width", 96), ("custom.align", "center"), ("custom.filterable", False),
                     ("custom.cellOptions", {"type": "color-background"}),
                     ("color", {"mode": "thresholds"}),
                     ("thresholds", {"mode": "absolute", "steps": [
                         {"color": "transparent", "value": None}, {"color": color, "value": 1}]}),
                     regex=True)
            for suffix, color in (("ok", "green"), ("skip", "orange"), ("fail", "red"))]


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


PLATFORM_COLORS = {"IN": "purple", "OUT": "#2f9e8f"}


def platform_cell(field="platform"):
    return override(field, ("mappings", [{"type": "value", "options": {
        code: {"color": c, "index": i} for i, (code, c) in enumerate(PLATFORM_COLORS.items())}}]),
        ("custom.cellOptions", {"type": "color-background"}), ("custom.width", 80))


def platform_var():
    """Platform filter shared by all dashboards (multi-select, All by default)."""
    v = query_var("platform", "Platform",
                  "SELECT code || ' — ' || name AS __text, code AS __value FROM platform ORDER BY code",
                  multi=True, include_all=True)
    v["current"] = {"text": ["All"], "value": ["$__all"]}
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
OVERVIEW_URL = "/d/kitchen-overview/campaign-overview?var-campaign=${campaign}&${platform:queryparam}&${__url_time_range}"


def status_filter(field, color, value):
    """Tile of the "Tests" stat: fixed color, click = filter the tests list on that status.
    Clears the "with action changes" filter, so the list matches the number on the tile."""
    return override(field, ("color", {"mode": "fixed", "fixedColor": color}), ("links", [{
        "title": f"Show {field} tests",
        "url": OVERVIEW_URL + "&var-test_status=" + value + "&var-only_changed=0"}]))


def comparison_row():
    """Tiles comparing the campaign with the previous campaign on the same platform."""
    previous = stat(
        "Previous campaign on this platform (click to open)",
        """SELECT coalesce(p.label || '  (' || p.status || ')', 'none: first campaign on this platform') AS previous,
       p.id AS prev_id
FROM v_campaign c LEFT JOIN v_campaign p ON p.id = c.prev_campaign_id
WHERE c.id = $campaign""", color_mode="none")
    previous["options"]["reduceOptions"]["fields"] = "/^previous$/"
    previous["options"].update({"textMode": "value", "text": {"valueSize": 20}})
    previous["fieldConfig"]["defaults"]["links"] = [{
        "title": "Open the previous campaign",
        "url": "/d/kitchen-overview/campaign-overview?var-campaign=${__data.fields.prev_id}"
               "&${platform:queryparam}&${__url_time_range}"}]

    pass_rate = stat(
        "Pass rate: previous / change",
        """SELECT p.pass_rate AS previous,
       CASE WHEN c.pass_rate = p.pass_rate THEN '±0.0 pts'
            ELSE to_char(c.pass_rate - p.pass_rate, 'FMSG990.0') || ' pts' END AS change
FROM v_campaign_summary c
JOIN v_campaign v ON v.id = c.campaign_id
JOIN v_campaign_summary p ON p.campaign_id = v.prev_campaign_id
WHERE c.campaign_id = $campaign""",
        overrides=[override("previous", ("unit", "percent"), ("color", {"mode": "fixed", "fixedColor": "text"})),
                   override("change", ("mappings", [
                       {"type": "regex", "options": {"pattern": "^\\+.*", "result": {"color": "green"}}},
                       {"type": "regex", "options": {"pattern": "^-.*", "result": {"color": "red"}}},
                       {"type": "regex", "options": {"pattern": ".*", "result": {"color": "text"}}}]))],
        color_mode="value")

    def alert_when_positive(field, color):
        return override(field, ("color", {"mode": "thresholds"}), ("thresholds", {"mode": "absolute", "steps": [
            {"color": "text", "value": None}, {"color": color, "value": 1}]}))
    transitions = stat(
        "Regressions / fixes vs previous run",
        """SELECT count(*) FILTER (WHERE transition = 'regression') AS regressions,
       count(*) FILTER (WHERE transition = 'fixed') AS fixes
FROM v_test_result WHERE campaign_id = $campaign""",
        overrides=[alert_when_positive("regressions", "red"), alert_when_positive("fixes", "green")],
        color_mode="value")

    changed = stat(
        "Tests with action changes (click to list them)",
        """SELECT count(*) FILTER (WHERE d.actions_changed > 0) AS "tests with action changes"
FROM test t LEFT JOIN v_test_diff d ON d.test_id = t.id
WHERE t.campaign_id = $campaign""",
        defaults={"color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": [
            {"color": "transparent", "value": None}, {"color": "orange", "value": 1}]},
                  "links": [{"title": "Show only the tests with action changes",
                             "url": OVERVIEW_URL + "&var-test_status=%24__all&var-only_changed=1"}]})
    changed["description"] = ("Tests where at least one action has a different status than in the previous run "
                              "of the same test on this platform (actions matched by step and action position).")
    changed["options"]["textMode"] = "value"
    return [(previous, 8), (pass_rate, 5), (transitions, 6), (changed, 5)]


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
              overrides=[status_filter("all", "blue", "%24__all"),
                         *[status_filter(st, c, st) for st, c in STATUS_COLORS.items()]]), 10),
        (stat("Duration", "SELECT duration_s AS duration FROM v_campaign_summary WHERE campaign_id = $campaign",
              defaults={"unit": "s"}, color_mode="none"), 3),
        h=4)
    L.row((versions_stat("id = $campaign"), 15), (changes_stat("id = $campaign"), 9), h=4)
    L.row(({"type": "row", "title": "Comparison with the previous campaign on this platform",
            "collapsed": False, "panels": []}, 24), h=1)
    L.row(*comparison_row(), h=4)
    L.row((table(
        "Tests: ${test_status:text}, ${only_changed:text} (click a name to drill down)",
        """SELECT t.id, t.name, t.status::text AS status, r.transition,
       d.steps_changed AS "steps changed", d.actions_changed AS "actions changed",
       s.ok AS "steps ok", s.skip AS "steps skip", s.fail AS "steps fail",
       a.ok AS "actions ok", a.skip AS "actions skip", a.fail AS "actions fail",
       r.duration_s / 60.0 AS duration
FROM test t
JOIN v_test_result r ON r.test_id = t.id
LEFT JOIN v_test_diff d ON d.test_id = t.id
CROSS JOIN LATERAL (
    SELECT count(*) FILTER (WHERE status = 'success') AS ok,
           count(*) FILTER (WHERE status = 'skipped') AS skip,
           count(*) FILTER (WHERE status = 'failed')  AS fail
    FROM step WHERE test_id = t.id) s
CROSS JOIN LATERAL (
    SELECT count(*) FILTER (WHERE a.status = 'success') AS ok,
           count(*) FILTER (WHERE a.status = 'skipped') AS skip,
           count(*) FILTER (WHERE a.status = 'failed')  AS fail
    FROM action a JOIN step st ON st.id = a.step_id WHERE st.test_id = t.id) a
WHERE t.campaign_id = $campaign AND t.status::text IN ($test_status)
  AND ($only_changed = 0 OR coalesce(d.actions_changed, 0) > 0)
ORDER BY t.rank""",
        desc="transition and steps/actions changed: compared with the previous run of the test on the same "
             "platform (actions whose status differs, and the steps containing them).",
        overrides=[color_cell("status"), color_cell("transition", TRANSITION_MAPPING),
                   override("id", ("custom.hidden", True)), test_link("name"),
                   override("name", ("custom.minWidth", 240)), width("status", 85), width("transition", 115),
                   override("duration", ("unit", "m"), ("decimals", 1), ("custom.width", 90)), *count_cells(),
                   override("(steps|actions) changed", ("custom.width", 110), ("custom.align", "center"),
                            ("custom.filterable", False), ("custom.cellOptions", {"type": "color-background"}),
                            ("color", {"mode": "thresholds"}),
                            ("thresholds", {"mode": "absolute", "steps": [
                                {"color": "transparent", "value": None}, {"color": "orange", "value": 1}]}),
                            regex=True)]),
           24), h=17)

    variables = [
        platform_var(),
        query_var("campaign", "Campaign",
                  "SELECT label || '  (' || status || ')' AS __text, id AS __value FROM v_campaign "
                  "WHERE platform IN ($platform) ORDER BY started_at DESC"),
        # set by clicking the "Tests" tiles; hidden because the tiles act as the filter buttons
        {"type": "custom", "name": "test_status", "label": "Test status", "hide": 2,
         "query": "success,skipped,failed", "multi": True, "includeAll": True,
         "current": {"text": ["All"], "value": ["$__all"]},
         "options": [{"text": "All", "value": "$__all", "selected": True}]
                    + [{"text": v, "value": v, "selected": False} for v in STATUS_COLORS]},
        # set by clicking the "Tests with action changes" tile (1) or the "all" tile (0)
        {"type": "custom", "name": "only_changed", "label": "Only tests with action changes", "hide": 2,
         "query": "all tests : 0, with action changes : 1",
         "current": {"text": "all tests", "value": "0"},
         "options": [{"text": "all tests", "value": "0", "selected": True},
                     {"text": "with action changes", "value": "1", "selected": False}]},
    ]
    return dashboard("kitchen-overview", "Campaign overview", L, variables)


# ---------------------------------------------------------------- 02 Trends
def trends():
    L = Layout()
    sel = "name IN ($tests) AND {p}platform IN ($platform)"
    platform_colors = [fixed_color(code, c) for code, c in PLATFORM_COLORS.items()]
    L.row((panel(
        "timeseries", "Pass rate per campaign, per platform",
        f"""SELECT c.started_at AS time, c.platform AS metric,
       round(100.0 * count(*) FILTER (WHERE t.status = 'success') / count(*), 1) AS value
FROM campaign c JOIN test t ON t.campaign_id = c.id
WHERE $__timeFilter(c.started_at) AND t.{sel.format(p="c.")}
GROUP BY c.id ORDER BY 1""",
        fmt="time_series",
        defaults={"unit": "percent", "min": 0, "max": 100,
                  "custom": {"drawStyle": "line", "lineWidth": 2, "showPoints": "always", "pointSize": 7,
                             "fillOpacity": 0, "spanNulls": True, "thresholdsStyle": {"mode": "line+area"}},
                  "thresholds": {"mode": "absolute", "steps": [
                      {"color": "transparent", "value": None}, {"color": "green", "value": 90}]}},
        overrides=platform_colors,
        options={"legend": {"showLegend": True, "placement": "bottom"}, "tooltip": {"mode": "multi"}}), 24), h=8)
    L.row((panel(
        "barchart", "Test results per campaign",
        f"""SELECT c.platform || ' ' || c.kms_version || ' ' || to_char(c.started_at, 'MM-DD') AS campaign,
       count(*) FILTER (WHERE t.status = 'success') AS success,
       count(*) FILTER (WHERE t.status = 'skipped') AS skipped,
       count(*) FILTER (WHERE t.status = 'failed') AS failed
FROM campaign c JOIN test t ON t.campaign_id = c.id
WHERE $__timeFilter(c.started_at) AND t.{sel.format(p="c.")}
GROUP BY c.id ORDER BY c.started_at""",
        defaults={"custom": {"fillOpacity": 85, "lineWidth": 0}},
        overrides=[fixed_color(s, c) for s, c in STATUS_COLORS.items()],
        options={"xField": "campaign", "stacking": "normal", "orientation": "vertical",
                 "xTickLabelRotation": -60, "xTickLabelSpacing": 0, "showValue": "never", "barWidth": 0.8,
                 "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"}}), 24), h=11)
    matrix = table(
        "Pass rate per test, platform and KMS version",
        f"""SELECT platform || ' · ' || name AS test, kms_version AS "KMS",
       round(100.0 * count(*) FILTER (WHERE status = 'success') / count(*)) AS pass_rate
FROM v_test_result
WHERE $__timeFilter(campaign_started_at) AND {sel.format(p="")}
GROUP BY platform, name, kms_version, kms_version_order
ORDER BY kms_version_order, platform, name""",
        desc="Share of successful runs of each test on each KMS version, per platform.",
        overrides=[override("test\\KMS", ("custom.cellOptions", {"type": "auto"}),
                            ("custom.width", 260), ("custom.align", "left"), ("unit", "string"))])
    matrix["fieldConfig"]["defaults"].update({
        "unit": "percent", "color": {"mode": "thresholds"},
        "custom": {"align": "center", "cellOptions": {"type": "color-background"}, "filterable": False,
                   "minWidth": 60},
        "thresholds": {"mode": "absolute", "steps": [
            {"color": "red", "value": None}, {"color": "orange", "value": 50}, {"color": "green", "value": 100}]}})
    matrix["transformations"] = [{"id": "groupingToMatrix", "options": {
        "columnField": "KMS", "rowField": "test", "valueField": "pass_rate", "emptyValue": "null"}}]
    L.row((matrix, 24), h=24)
    L.row(
        (panel("timeseries", "Campaign duration",
               """SELECT started_at AS time, platform AS metric, duration_s / 60.0 AS value
FROM v_campaign_summary WHERE $__timeFilter(started_at) AND platform IN ($platform) ORDER BY 1""",
               fmt="time_series",
               defaults={"unit": "m", "custom": {"drawStyle": "line", "showPoints": "always", "pointSize": 6}},
               overrides=platform_colors,
               options={"legend": {"showLegend": True, "placement": "bottom"}}), 12),
        (panel("timeseries", "Test duration",
               f"""SELECT campaign_started_at AS time, platform || ' · ' || name AS metric, duration_s / 60.0 AS value
FROM v_test_result WHERE $__timeFilter(campaign_started_at) AND {sel.format(p="")} ORDER BY 1""",
               fmt="time_series",
               defaults={"unit": "m", "custom": {"drawStyle": "line", "showPoints": "always", "pointSize": 4}},
               options={"legend": {"showLegend": True, "displayMode": "table", "placement": "right",
                                   "calcs": ["mean", "max"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}}), 12),
        h=10)
    tests_var = query_var("tests", "Tests", "SELECT DISTINCT name FROM test ORDER BY 1",
                          multi=True, include_all=True)
    tests_var["current"] = {"text": ["All"], "value": ["$__all"]}
    return dashboard("kitchen-trends", "Trends over time", L, [platform_var(), tests_var],
                     annotations=release_annotations())


# ---------------------------------------------------------------- 03 Regressions
def regressions():
    L = Layout()
    plat = "platform IN ($platform)"
    L.row(
        (panel("barchart", "Regressions & fixes per KMS version (vs previous campaign on the same platform)",
               f"""SELECT kms_version AS "KMS",
       count(*) FILTER (WHERE transition = 'regression') AS regressions,
       count(*) FILTER (WHERE transition = 'fixed') AS fixes
FROM v_test_result WHERE $__timeFilter(campaign_started_at) AND {plat}
GROUP BY kms_version, kms_version_order ORDER BY kms_version_order""",
               defaults={"custom": {"fillOpacity": 85, "lineWidth": 0}},
               overrides=[fixed_color("regressions", "red"), fixed_color("fixes", "green")],
               options={"xField": "KMS", "stacking": "none", "showValue": "auto", "barWidth": 0.8,
                        "groupWidth": 0.8, "legend": {"showLegend": True, "placement": "bottom"}}), 14),
        (panel("bargauge", "Flakiest tests (status flips, last 15 campaigns per platform)",
               f"SELECT platform || ' · ' || name AS test, flips FROM v_flakiness WHERE flips > 0 AND {plat} "
               "ORDER BY flips DESC, 1 LIMIT 10",
               defaults={"min": 0, "color": {"mode": "continuous-YlRd"}},
               options={"reduceOptions": {"values": True, "calcs": [], "fields": "/^flips$/"},
                        "displayMode": "gradient", "orientation": "horizontal", "showUnfilled": True,
                        "valueMode": "color", "namePlacement": "left", "sizing": "auto"}), 10),
        h=9)
    history = panel(
        "status-history",
        "Test status history on $platform: green = success, orange = skipped, red = failed (one column per campaign)",
        f"""SELECT campaign_started_at AS time, name AS metric,
       CASE status WHEN 'success' THEN 0 WHEN 'skipped' THEN 1 ELSE 2 END AS value
FROM v_test_result WHERE $__timeFilter(campaign_started_at) AND platform IN (${{platform:sqlstring}})
ORDER BY 1, 2""",
        fmt="time_series",
        defaults={"mappings": STATUS_NUM_MAPPING, "color": {"mode": "thresholds"},
                  "custom": {"fillOpacity": 85, "lineWidth": 1}},
        options={"showValue": "never", "rowHeight": 0.85, "colWidth": 0.95,
                 "legend": {"showLegend": False}})
    history.update({"repeat": "platform", "repeatDirection": "v"})   # one panel per selected platform
    L.row((history, 24), h=12)
    L.row((table(
        "KMS version comparison: $base → $target (failure rate per test and platform)",
        f"""WITH r AS (
    SELECT name, platform, kms_version,
           100.0 * count(*) FILTER (WHERE status = 'failed') / count(*) AS fail_rate,
           count(*) AS runs
    FROM v_test_result WHERE kms_version IN ('$base', '$target') AND {plat}
    GROUP BY name, platform, kms_version
)
SELECT b.platform, b.name,
       round(b.fail_rate) AS "base fail %", b.runs AS "base runs",
       round(t.fail_rate) AS "target fail %", t.runs AS "target runs",
       round(t.fail_rate - b.fail_rate) AS "delta %",
       CASE WHEN t.fail_rate - b.fail_rate >= 50 THEN 'regression'
            WHEN b.fail_rate - t.fail_rate >= 50 THEN 'fixed'
            WHEN t.fail_rate > 0 THEN 'still_failing'
            ELSE 'stable_ok' END AS verdict
FROM r b JOIN r t ON t.name = b.name AND t.platform = b.platform
WHERE b.kms_version = '$base' AND t.kms_version = '$target'
ORDER BY "delta %" DESC, b.name, b.platform""",
        desc="Compares the share of failed runs of each test between two KMS versions, per platform. "
             "A delta >= 50 points is flagged as a regression.",
        overrides=[color_cell("verdict", TRANSITION_MAPPING), platform_cell(),
                   override("delta %", ("custom.cellOptions", {"type": "color-text"}),
                            ("color", {"mode": "thresholds"}),
                            ("thresholds", {"mode": "absolute", "steps": [
                                {"color": "green", "value": None}, {"color": "text", "value": 0},
                                {"color": "red", "value": 1}]}))]), 24), h=12)
    L.row((table(
        "Regressions (success → failed) with what changed and root cause",
        f"""SELECT r.test_id AS id, r.campaign_started_at AS date, r.platform, r.name,
       r.changes AS "changed since previous run",
       f.step_position || '.' || f.action_position AS "at",
       split_part(f.action_description, E'\\n', 1) AS action,
       f.expected_result AS expected, f.actual_result AS actual
FROM v_test_result r LEFT JOIN v_first_failure f ON f.test_id = r.test_id
WHERE r.transition = 'regression' AND $__timeFilter(r.campaign_started_at) AND r.{plat}
ORDER BY 2 DESC, r.rank""",
        desc="'changed since previous run' lists the software versions that differ from the previous "
             "campaign on the same platform: the suspects for the regression.",
        overrides=[override("id", ("custom.hidden", True)), width("date", 160), platform_cell(),
                   override("name", ("custom.width", 220)), width("at", 50), test_link("name"),
                   wrap("changed since previous run", "action", "expected", "actual")]), 24), h=14)
    L.row((table(
        "Fixes (failed → success) with what changed",
        f"""SELECT r.test_id AS id, r.campaign_started_at AS date, r.platform, r.name,
       r.changes AS "changed since previous run"
FROM v_test_result r
WHERE r.transition = 'fixed' AND $__timeFilter(r.campaign_started_at) AND r.{plat}
ORDER BY 2 DESC, r.rank""",
        overrides=[override("id", ("custom.hidden", True)), width("date", 160), platform_cell(),
                   override("name", ("custom.width", 220)), test_link("name"),
                   wrap("changed since previous run")]), 24), h=10)

    kms_versions = ("SELECT kms_version FROM campaign GROUP BY kms_version, kms_version_order "
                    "ORDER BY kms_version_order DESC")
    target_var = query_var("target", "Target KMS", kms_versions)
    base_var = query_var("base", "Base KMS", kms_versions + " OFFSET 1")
    return dashboard("kitchen-regressions", "Regressions", L, [platform_var(), base_var, target_var],
                     annotations=release_annotations())


# ---------------------------------------------------------------- 04 Test details
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
    L.row((table(
        "Steps",
        """SELECT position AS "#", description, expected_result AS expected, actual_result AS actual,
       status::text AS status
FROM step WHERE test_id = $test ORDER BY position""",
        overrides=[color_cell("status"), width("#", 50), width("status", 90),
                   wrap("description", "expected", "actual")]), 24), h=12)
    L.row((table(
        "Actions",
        """SELECT s.position || '.' || a.position AS "#", split_part(s.description, E'\\n', 1) AS step, a.description AS action,
       a.expected_result AS expected, a.actual_result AS actual, a.status::text AS status,
       a.duration_ms / 1000.0 AS "duration (s)"
FROM action a JOIN step s ON s.id = a.step_id
WHERE s.test_id = $test ORDER BY s.position, a.position""",
        overrides=[color_cell("status"), width("#", 50), width("step", 200), width("status", 90),
                   width("duration (s)", 120), wrap("step", "action", "expected", "actual")]), 24), h=20)
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
        "ORDER BY c.started_at DESC, t.rank")]
    return dashboard("kitchen-test", "Test details", L, variables)


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
                         ("03_regressions.json", regressions),
                         ("04_test_details.json", test_details)]:
        dash = build()
        (OUT / fname).write_text(json.dumps(dash, indent=2, ensure_ascii=False) + "\n")
        (EXPORT / fname).write_text(json.dumps(portable(dash), indent=2, ensure_ascii=False) + "\n")
        print(OUT / fname, "+ export")


if __name__ == "__main__":
    main()
