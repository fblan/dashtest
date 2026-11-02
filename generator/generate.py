#!/usr/bin/env python3
"""Generate simulated kitchen test campaigns as SQL (db/init/03_seed.sql).

Stdlib only and deterministic for a given --seed.
Only action statuses are generated; step/test/campaign statuses are derived
in SQL by recompute_status().
"""
import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scenarios import (BASE_FAIL_P, COMPONENT_RELEASES, ENV_SKIP_P, ENV_SKIP_REASONS, KMS_VERSIONS,
                       LATEST_CAMPAIGNS, PLATFORMS, REGRESSIONS, SCENARIOS)

OUT = Path(__file__).resolve().parent.parent / "db" / "init" / "03_seed.sql"


def vkey(v):
    return tuple(int(x) for x in v.split("."))


def version_rank(component, version):
    """Comparable rank of a component version."""
    if component == "kms":
        return vkey(version)
    return [v for _, v in COMPONENT_RELEASES[component]].index(version)


def component_versions(day):
    """Versions of the non-KMS components in use on a given day."""
    return {comp: [v for d, v in releases if datetime.fromisoformat(d).replace(tzinfo=timezone.utc) <= day][-1]
            for comp, releases in COMPONENT_RELEASES.items()}


def regression_p(tag, versions, platform):
    """Failure probability added by a scripted regression, given the campaign's versions and platform."""
    if tag not in REGRESSIONS:
        return 0.0
    component, start, end, p, platforms = REGRESSIONS[tag]
    if platforms is not None and platform not in platforms:
        return 0.0
    rank = version_rank(component, versions[component])
    if rank >= version_rank(component, start) and (end is None or rank < version_rank(component, end)):
        return p
    return 0.0


def q(s):
    return "NULL" if s is None else "'" + str(s).replace("'", "''") + "'"


def ts(d):
    return q(d.isoformat())


def fmt_num(value, tol, unit):
    decimals = 0 if tol >= 2 or (tol == 0 and float(value).is_integer()) else (1 if tol >= 0.1 or tol == 0 else 2)
    text = f"{value:.{decimals}f}"
    if not unit:
        return text
    return text + unit if unit in ("°C", "%", "x") else f"{text} {unit}"


def expected_line(check):
    if check[0] == "num":
        _, label, nominal, tol, unit, _ = check
        return f"{label}: {fmt_num(nominal, tol, unit)}" + (f" ±{fmt_num(tol, tol, unit)}" if tol else "")
    return f"{check[1]}: {check[2]}"


def actual_line(check, ok, rng):
    """Measured result of one check; inside the tolerance when ok, around the faulty value otherwise."""
    if check[0] == "txt":
        return f"{check[1]}: {check[2] if ok else check[3]}"
    _, label, nominal, tol, unit, bad = check
    if ok:
        value = nominal + rng.uniform(-0.6, 0.6) * tol
    else:
        value = bad + rng.uniform(-0.3, 0.3) * tol
        if abs(value - nominal) <= tol:
            value = bad
    if tol == 0 and float(nominal).is_integer():
        value = round(value)
    return f"{label}: {fmt_num(value, tol, unit)}" + ("" if ok else "  ✗ out of tolerance")


def generate(seed, start, end):
    rng = random.Random(seed)
    # Campaign schedule: KMS releases spread over the period on each platform.
    # IN runs at night (around 22:00), OUT during the day (around 10:00).
    schedule = []
    for p_idx, platform in enumerate(PLATFORMS):
        plan = [v for v, *counts in KMS_VERSIONS for _ in range(counts[p_idx])]
        span = (end - start) / len(plan)
        hour = 22 if platform == "IN" else 10
        for i, kms in enumerate(plan):
            day = (start + span * i).replace(hour=hour, minute=0, second=0, microsecond=0)
            schedule.append((day + timedelta(minutes=rng.randint(-120, 120)), platform, kms, True))
    schedule.sort()
    # then the latest campaigns at fixed dates, without random noise
    schedule += [(datetime.fromisoformat(d).replace(tzinfo=timezone.utc), platform, kms, False)
                 for d, platform, kms in LATEST_CAMPAIGNS]

    campaigns, tests, steps, actions = [], [], [], []
    test_id = step_id = action_id = 0

    for c_idx, (c_start, platform, kms, noisy) in enumerate(schedule, start=1):
        versions = {"kms": kms, **component_versions(c_start)}
        fail_p, skip_p = (BASE_FAIL_P, ENV_SKIP_P) if noisy else (0.0, 0.0)
        t_cursor = c_start

        for rank, slug, scenario_steps in SCENARIOS:
            test_id += 1
            t_start = t_cursor
            broken = False  # once an action fails/skips, everything after is skipped

            stop_at = None   # "step.action" where the test stopped
            for s_pos, step in enumerate(scenario_steps, start=1):
                step_id += 1
                step_actual = None
                n_actions = len(step["actions"])
                for a_pos, action in enumerate(step["actions"], start=1):
                    action_id += 1
                    checks = action["checks"]
                    expected = "\n".join(expected_line(c) for c in checks)
                    if broken:
                        status, actual, dur = "skipped", None, 0
                    else:
                        dur = rng.randint(5_000, 240_000)
                        r = rng.random()
                        p_regression = regression_p(action["tag"], versions, platform)
                        if r < p_regression + fail_p:
                            # a regression breaks the first check, a random failure any check
                            bad = 0 if r < p_regression else rng.randrange(len(checks))
                            lines = [actual_line(c, i != bad, rng) for i, c in enumerate(checks)]
                            status, actual = "failed", "\n".join(lines)
                            step_actual = (f"FAILED at action {s_pos}.{a_pos}\n{lines[bad].replace('  ✗ out of tolerance', '')}"
                                           + (f"\n{n_actions - a_pos} remaining action(s) skipped" if a_pos < n_actions else ""))
                        elif r < p_regression + fail_p + skip_p:
                            reason = rng.choice(ENV_SKIP_REASONS)
                            status, actual, dur = "skipped", reason, 0
                            step_actual = f"SKIPPED at action {s_pos}.{a_pos}\n{reason}"
                        else:
                            status, actual = "success", "\n".join(actual_line(c, True, rng) for c in checks)
                        if status != "success":
                            broken, stop_at = True, f"{s_pos}.{a_pos}"
                    actions.append((action_id, step_id, a_pos, action["procedure"], expected, actual, status, dur))
                    t_cursor += timedelta(milliseconds=dur)

                if step_actual is None:
                    step_actual = (f"Not executed: test stopped at action {stop_at}" if broken
                                   else f"OK: all {n_actions} actions passed")
                steps.append((step_id, test_id, s_pos, step["description"], step["expected"], step_actual))

            t_cursor += timedelta(seconds=rng.randint(10, 60))  # setup between tests
            tests.append((test_id, c_idx, rank, f"{rank:02d}_{slug}", t_start, t_cursor))

        campaigns.append((c_idx, platform, versions, c_start, t_cursor))

    return campaigns, tests, steps, actions


def batched_insert(f, table, cols, rows, fmt, size=500):
    for i in range(0, len(rows), size):
        f.write(f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n")
        f.write(",\n".join(fmt(r) for r in rows[i:i + size]))
        f.write(";\n")


def write_sql(path, campaigns, tests, steps, actions):
    order = {v: i for i, v in enumerate(sorted({c[2]["kms"] for c in campaigns}, key=vkey), start=1)}
    with open(path, "w") as f:
        f.write("-- GENERATED by generator/generate.py - do not edit\nBEGIN;\n")
        batched_insert(f, "campaign", ["id", "platform", "kms_version", "kms_version_order", "kes_version",
                                       "ktrs_version", "gateway_version", "testcampaign_version",
                                       "started_at", "ended_at"], campaigns,
                       lambda r: f"({r[0]}, {q(PLATFORMS[r[1]])}, {q(r[2]['kms'])}, {order[r[2]['kms']]}, {q(r[2]['kes'])}, "
                                 f"{q(r[2]['ktrs'])}, {q(r[2]['gateway'])}, {q(r[2]['testcampaign'])}, "
                                 f"{ts(r[3])}, {ts(r[4])})")
        batched_insert(f, "test", ["id", "campaign_id", "rank", "name", "started_at", "ended_at"], tests,
                       lambda r: f"({r[0]}, {r[1]}, {r[2]}, {q(r[3])}, {ts(r[4])}, {ts(r[5])})")
        batched_insert(f, "step", ["id", "test_id", "position", "description", "expected_result", "actual_result"], steps,
                       lambda r: f"({r[0]}, {r[1]}, {r[2]}, {q(r[3])}, {q(r[4])}, {q(r[5])})")
        batched_insert(f, "action", ["id", "step_id", "position", "description", "expected_result", "actual_result",
                                     "status", "duration_ms"], actions,
                       lambda r: f"({r[0]}, {r[1]}, {r[2]}, {q(r[3])}, {q(r[4])}, {q(r[5])}, {q(r[6])}, {r[7]})")
        for t in ("campaign", "test", "step", "action"):
            f.write(f"SELECT setval('{t}_id_seq', (SELECT max(id) FROM {t}));\n")
        f.write("SELECT recompute_status();\nCOMMIT;\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2026-04-01", help="first campaign date (YYYY-MM-DD)")
    ap.add_argument("--end", default="2026-10-30", help="last campaign date (YYYY-MM-DD)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    data = generate(args.seed, start, end)
    write_sql(args.out, *data)
    print(f"{args.out}: {len(data[0])} campaigns, {len(data[1])} tests, {len(data[2])} steps, {len(data[3])} actions")


if __name__ == "__main__":
    main()
