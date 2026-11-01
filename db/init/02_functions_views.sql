-- Functions and views. Idempotent: run at first start (initdb) and re-applied on every deploy
-- by the "migrate" service, so changes here reach an existing database. Data is not touched.
DROP VIEW IF EXISTS v_test_diff, v_flakiness, v_campaign_summary, v_test_result, v_campaign,
    v_first_failure, check_cascade, v_action_ordered CASCADE;
DROP FUNCTION IF EXISTS test_diff(int, int) CASCADE;

-- Status derivation: any failed -> failed, else any skipped -> skipped, else success
CREATE OR REPLACE FUNCTION agg_status(failed bool, skipped bool) RETURNS exec_status
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN failed THEN 'failed'::exec_status
                WHEN skipped THEN 'skipped'::exec_status
                ELSE 'success'::exec_status END
$$;

CREATE OR REPLACE FUNCTION recompute_status() RETURNS void
LANGUAGE sql AS $$
    UPDATE step s SET status = a.st
    FROM (SELECT step_id,
                 agg_status(bool_or(status = 'failed'), bool_or(status = 'skipped')) AS st
          FROM action GROUP BY step_id) a
    WHERE a.step_id = s.id;

    UPDATE test t SET status = s.st
    FROM (SELECT test_id,
                 agg_status(bool_or(status = 'failed'), bool_or(status = 'skipped')) AS st
          FROM step GROUP BY test_id) s
    WHERE s.test_id = t.id;

    UPDATE campaign c SET status = t.st
    FROM (SELECT campaign_id,
                 agg_status(bool_or(status = 'failed'), bool_or(status = 'skipped')) AS st
          FROM test GROUP BY campaign_id) t
    WHERE t.campaign_id = c.id;
$$;

-- Actions in test execution order (global ordinal across steps)
CREATE VIEW v_action_ordered AS
SELECT a.*, s.test_id, s.position AS step_position,
       row_number() OVER (PARTITION BY s.test_id ORDER BY s.position, a.position) AS ord
FROM action a JOIN step s ON s.id = a.step_id;

-- Cascade rule check: once an action is failed/skipped, every later action of the test must be skipped.
-- Must always return 0 rows.
CREATE VIEW check_cascade AS
SELECT later.*
FROM v_action_ordered later
WHERE later.status <> 'skipped'
  AND EXISTS (SELECT 1 FROM v_action_ordered earlier
              WHERE earlier.test_id = later.test_id
                AND earlier.ord < later.ord
                AND earlier.status IN ('failed', 'skipped'));

-- First non-success action of each test: the root cause
CREATE VIEW v_first_failure AS
SELECT DISTINCT ON (o.test_id)
       o.test_id, o.step_position, s.description AS step_description,
       o.position AS action_position, o.description AS action_description,
       o.expected_result, o.actual_result, o.status
FROM v_action_ordered o JOIN step s ON s.id = o.step_id
WHERE o.status <> 'success'
ORDER BY o.test_id, o.ord;

-- Campaign with a readable label, the component versions changed since the previous
-- campaign on the same platform, and the most recent campaign where versions changed
CREATE VIEW v_campaign AS
WITH c AS (
SELECT c.*,
       c.platform || ' · KMS ' || c.kms_version || ' · ' || to_char(c.started_at, 'YYYY-MM-DD HH24:MI') AS label,
       lag(c.id) OVER w AS prev_campaign_id,
       coalesce(nullif(concat_ws(', ',
           CASE WHEN lag(c.kms_version) OVER w <> c.kms_version
                THEN 'KMS ' || lag(c.kms_version) OVER w || ' → ' || c.kms_version END,
           CASE WHEN lag(c.kes_version) OVER w <> c.kes_version
                THEN 'KES ' || lag(c.kes_version) OVER w || ' → ' || c.kes_version END,
           CASE WHEN lag(c.ktrs_version) OVER w <> c.ktrs_version
                THEN 'KTRS ' || lag(c.ktrs_version) OVER w || ' → ' || c.ktrs_version END,
           CASE WHEN lag(c.gateway_version) OVER w <> c.gateway_version
                THEN 'Gateway ' || lag(c.gateway_version) OVER w || ' → ' || c.gateway_version END,
           CASE WHEN lag(c.testcampaign_version) OVER w <> c.testcampaign_version
                THEN 'TestCampaign ' || lag(c.testcampaign_version) OVER w || ' → ' || c.testcampaign_version END
       ), ''), CASE WHEN lag(c.id) OVER w IS NULL THEN 'first campaign on ' || c.platform ELSE 'no change' END)
           AS changes
FROM campaign c
WINDOW w AS (PARTITION BY c.platform ORDER BY c.started_at)
)
SELECT c.*, lc.changes AS last_change, lc.started_at AS last_change_at
FROM c
LEFT JOIN LATERAL (
    SELECT c2.changes, c2.started_at FROM c c2
    WHERE c2.platform = c.platform AND c2.started_at <= c.started_at AND c2.changes <> 'no change'
    ORDER BY c2.started_at DESC LIMIT 1
) lc ON true;

-- One row per test execution, with transition from the previous campaign on the same platform
-- and the component versions that changed since then.
CREATE VIEW v_test_result AS
SELECT t.id AS test_id, t.name, t.rank, t.status,
       t.started_at, t.ended_at,
       extract(epoch FROM t.ended_at - t.started_at) AS duration_s,
       c.id AS campaign_id, c.platform, c.started_at AS campaign_started_at,
       c.kms_version, c.kms_version_order, c.kes_version, c.ktrs_version, c.gateway_version,
       c.testcampaign_version,
       lag(t.id) OVER w AS prev_test_id,
       lag(t.status) OVER w AS prev_status,
       lag(c.kms_version) OVER w AS prev_kms_version,
       concat_ws(', ',
           CASE WHEN lag(c.kms_version) OVER w <> c.kms_version
                THEN 'KMS ' || lag(c.kms_version) OVER w || ' → ' || c.kms_version END,
           CASE WHEN lag(c.kes_version) OVER w <> c.kes_version
                THEN 'KES ' || lag(c.kes_version) OVER w || ' → ' || c.kes_version END,
           CASE WHEN lag(c.ktrs_version) OVER w <> c.ktrs_version
                THEN 'KTRS ' || lag(c.ktrs_version) OVER w || ' → ' || c.ktrs_version END,
           CASE WHEN lag(c.gateway_version) OVER w <> c.gateway_version
                THEN 'Gateway ' || lag(c.gateway_version) OVER w || ' → ' || c.gateway_version END,
           CASE WHEN lag(c.testcampaign_version) OVER w <> c.testcampaign_version
                THEN 'TestCampaign ' || lag(c.testcampaign_version) OVER w || ' → ' || c.testcampaign_version END
       ) AS changes,
       CASE
           WHEN lag(t.status) OVER w IS NULL THEN 'new'
           WHEN lag(t.status) OVER w = 'success' AND t.status = 'failed' THEN 'regression'
           WHEN lag(t.status) OVER w = 'failed' AND t.status = 'success' THEN 'fixed'
           WHEN t.status = 'success' THEN 'stable_ok'
           WHEN t.status = 'failed' THEN 'still_failing'
           ELSE 'skipped'
       END AS transition
FROM test t JOIN campaign c ON c.id = t.campaign_id
WINDOW w AS (PARTITION BY t.name, c.platform ORDER BY c.started_at);

CREATE VIEW v_campaign_summary AS
SELECT c.id AS campaign_id, c.platform, c.label, c.changes,
       c.kms_version, c.kms_version_order, c.kes_version, c.ktrs_version, c.gateway_version,
       c.testcampaign_version, c.started_at, c.ended_at, c.status,
       extract(epoch FROM c.ended_at - c.started_at) AS duration_s,
       count(*) FILTER (WHERE t.status = 'success') AS n_success,
       count(*) FILTER (WHERE t.status = 'skipped') AS n_skipped,
       count(*) FILTER (WHERE t.status = 'failed')  AS n_failed,
       count(*) AS n_tests,
       round(100.0 * count(*) FILTER (WHERE t.status = 'success') / count(*), 1) AS pass_rate
FROM v_campaign c JOIN test t ON t.campaign_id = c.id
GROUP BY c.id, c.platform, c.label, c.changes, c.kms_version, c.kms_version_order, c.kes_version,
         c.ktrs_version, c.gateway_version, c.testcampaign_version, c.started_at, c.ended_at, c.status;

-- Flakiness: status flips over the last 15 campaigns of each platform
CREATE VIEW v_flakiness AS
WITH recent_campaigns AS (
    SELECT id FROM (
        SELECT id, row_number() OVER (PARTITION BY platform ORDER BY started_at DESC) AS n FROM campaign
    ) r WHERE n <= 15
), flips AS (
    SELECT name, platform, status,
           lag(status) OVER (PARTITION BY name, platform ORDER BY campaign_started_at) AS prev
    FROM v_test_result WHERE campaign_id IN (SELECT id FROM recent_campaigns)
)
SELECT name, platform,
       count(*) FILTER (WHERE prev IS NOT NULL AND prev <> status) AS flips,
       count(*) FILTER (WHERE status = 'failed') AS failures,
       count(*) AS runs
FROM flips GROUP BY name, platform;

-- Differences between a test run and a base run of the same test, action by action (matched by step and
-- action position): actions whose status changed, regressed (success -> skipped/failed) or were fixed
-- (skipped/failed -> success), and the number of steps containing them. No row when base_test is NULL.
CREATE FUNCTION test_diff(cur_test int, base_test int)
RETURNS TABLE (actions_changed bigint, steps_changed bigint, actions_regressed bigint, steps_regressed bigint,
               actions_fixed bigint, steps_fixed bigint)
LANGUAGE sql STABLE STRICT AS $$
SELECT count(*) FILTER (WHERE a.status IS DISTINCT FROM pa.status),
       count(DISTINCT s.position) FILTER (WHERE a.status IS DISTINCT FROM pa.status),
       count(*) FILTER (WHERE pa.status = 'success' AND a.status <> 'success'),
       count(DISTINCT s.position) FILTER (WHERE pa.status = 'success' AND a.status <> 'success'),
       count(*) FILTER (WHERE pa.status <> 'success' AND a.status = 'success'),
       count(DISTINCT s.position) FILTER (WHERE pa.status <> 'success' AND a.status = 'success')
FROM step s
JOIN action a ON a.step_id = s.id
LEFT JOIN step ps ON ps.test_id = base_test AND ps.position = s.position
LEFT JOIN action pa ON pa.step_id = ps.id AND pa.position = a.position
WHERE s.test_id = cur_test
$$;

-- test_diff with the previous run of the same test on the same platform
CREATE VIEW v_test_diff AS
SELECT r.test_id, r.prev_test_id, d.*
FROM v_test_result r CROSS JOIN LATERAL test_diff(r.test_id, r.prev_test_id) d;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
