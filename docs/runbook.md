# cardguard runbook

Operational responses for the most common failure modes. All logs are structured
JSON with a `request_id` that matches the `X-Request-Id` response header (or the
`request_id` workflow input for activity logs).

## p99 latency breaches the 250ms budget

Symptom: `over_budget: true` entries in the logs and rising `cardguard_auth_latency_seconds` p99.

1. Look at the `stages` breakdown in the WARN log line: `policy`, `model`, `ledger`,
   or `idempotency` tells you where the time went.
   - `policy` slow: run the queries from `docs/query-plans.md` with EXPLAIN ANALYZE;
     check for missing indexes (`ix_ledger_entry_created_at`,
     `ix_authorization_card_id_created_at`) and vacuum/analyze on `ledger_entry`.
   - `model` slow: check the card history size (the serving path reads up to 1,000
     prior authorizations per request); if RDS IOPS are pegged, raise storage or
     tune `HISTORY_LIMIT`.
   - `idempotency` slow: contention on the idempotency unique index under a retry
     storm — this is expected tail behavior; raise throughput or shard the key space.
2. Check DB pool saturation: `SELECT count(*) FROM pg_stat_activity` — the API uses
   QueuePool(10 + 20 overflow) per process.
3. Scale out: bump `api_task_count` in `infra/environments/<env>` or add gunicorn
   threads (`--threads`) before adding workers.

## The model file fails to load

Symptom: `risk model file missing at ...` or `failed to load risk model` ERROR at
startup; `risk_score: null` in authorization responses; `cardguard_model_score`
histogram stops moving.

1. Confirm the artifact exists in the running image/volume
   (`ml/artifacts/model_v1.joblib`) and its `schema_version` matches
   `app/risk/features.py::FEATURE_SCHEMA_VERSION`.
2. Retrain or restore: `make data && make train` and redeploy, or restore the
   previous artifact.
3. Until it is fixed, decisioning continues policy-only — this is the designed
   fallback, so treat it as degraded-but-safe. The runbook action is: fix or roll
   back the artifact, then restart the api service so the model is reloaded at
   startup.

## Temporal worker backs up

Symptom: review workflows sit in `pending_review` past the review SLA; the Temporal
UI shows a growing backlog on `cardguard-risk`; `cardguard_activity_failures_total`
rising.

1. Check the worker's structured logs for repeated retries (retry policy: 5 attempts,
   1s initial, 2.0 backoff, `ValidationError` non-retryable). Non-retryable failures
   mean a data-state bug (e.g. authorization already transitioned) — fix the data,
   not the worker.
2. Confirm the DB is reachable from the worker task (the RDS security group allows
   the ECS task security group on 5432) and that `DATABASE_URL` resolves from the
   worker container.
3. Scale workers: `worker_task_count` in Terraform. Workflows are durable — killing
   or restarting the worker is safe; it resumes exactly where it stopped.

## Ledger invariant test fails in CI

Symptom: `tests/test_invariants.py` fails (balance mismatch, negative account, or
captured > held).

1. Never "fix" the test to make it pass — the invariant failing means the ledger is
   broken or the operation ordering changed.
2. Reproduce locally: `uv run pytest tests/test_invariants.py -x --tb=long`; the
   falsifying example prints the exact operation sequence that broke the invariant.
3. Replay the sequence against the ledger service and inspect the postings. If a
   change to the ledger touched `app/ledger/service.py`, review it for double
   postings or missing legs before anything else.
4. If the failure is only in CI, check for a skipped migration: the test database is
   created via `Base.metadata.create_all`, so model changes without a migration can
   pass locally and fail differently in CI.
