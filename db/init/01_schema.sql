-- Kitchen QA test results schema
-- campaign -> test -> step -> action

CREATE TYPE exec_status AS ENUM ('success', 'skipped', 'failed');

CREATE TABLE campaign (
    id                   serial PRIMARY KEY,
    platform             text        NOT NULL,      -- name of the kitchen platform the campaign runs on
    -- versions of the 5 software components used by the campaign
    kms_version          text        NOT NULL,      -- Kitchen Management System (system under test)
    kms_version_order    int         NOT NULL,      -- numeric ordering of KMS versions (1.2.0 < 1.10.0)
    kes_version          text        NOT NULL,      -- KitEquipmentSimulator
    ktrs_version         text        NOT NULL,      -- Kitchen Test Runner System
    gateway_version      text        NOT NULL,      -- Gateway
    testcampaign_version text        NOT NULL,      -- TestCampaign (test definitions)
    started_at           timestamptz NOT NULL,
    ended_at             timestamptz NOT NULL,
    status               exec_status,               -- derived by recompute_status()
    CHECK (ended_at >= started_at)
);
CREATE INDEX campaign_started_at_idx ON campaign (started_at);
CREATE INDEX campaign_platform_idx ON campaign (platform, started_at);

CREATE TABLE test (
    id          serial PRIMARY KEY,
    campaign_id int         NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    rank        smallint    NOT NULL CHECK (rank BETWEEN 0 AND 99),
    name        text        NOT NULL CHECK (name ~ '^\d{2}_'),  -- e.g. 03_oven_beef_wellington
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz NOT NULL,
    status      exec_status,                        -- derived from steps
    UNIQUE (campaign_id, rank),
    CHECK (name LIKE lpad(rank::text, 2, '0') || '\_%')
);
CREATE INDEX test_campaign_idx ON test (campaign_id);
CREATE INDEX test_name_idx ON test (name);

CREATE TABLE step (
    id              serial PRIMARY KEY,
    test_id         int      NOT NULL REFERENCES test (id) ON DELETE CASCADE,
    position        smallint NOT NULL,
    description     text     NOT NULL,
    expected_result text     NOT NULL,
    actual_result   text,
    status          exec_status,                    -- derived from actions
    UNIQUE (test_id, position)
);
CREATE INDEX step_test_idx ON step (test_id);

CREATE TABLE action (
    id              serial PRIMARY KEY,
    step_id         int         NOT NULL REFERENCES step (id) ON DELETE CASCADE,
    position        smallint    NOT NULL,
    description     text        NOT NULL,
    expected_result text        NOT NULL,
    actual_result   text,
    status          exec_status NOT NULL,
    duration_ms     int         NOT NULL DEFAULT 0,
    UNIQUE (step_id, position)
);
CREATE INDEX action_step_idx ON action (step_id);

-- Read-only role for Grafana: created by 00_grafana_role.sh (password from GRAFANA_RO_PASSWORD)
