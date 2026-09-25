# Contributing to cardguard

Thanks for taking the time to contribute.

## Setup

```sh
git clone <repo> && cd cardguard
make up && make migrate && make seed   # requires Docker
make test
```

Python 3.12, pinned via `uv` (`uv.lock` is committed). Tests need a local Postgres
16; `make test` starts the compose postgres for you.

## Workflow

1. Open an issue describing the bug or the design for a feature.
2. Branch, implement, and keep commits small and self-contained.
3. Before opening a PR:

   ```sh
   uv run ruff check app workflows tests loadtest scripts ml
   uv run ruff format --check app workflows tests loadtest scripts ml
   uv run mypy --strict app
   uv run pytest --cov=app --cov=workflows --cov-fail-under=80
   uv run bandit -c pyproject.toml -r app workflows
   ```

   CI enforces the same gates plus `pip-audit`.

## Conventions

- Money is `BIGINT` integer cents end to end. Never floats, never `DECIMAL` columns.
- Every ledger posting must balance: sum(debits) == sum(credits) within an
  (authorization_id, entry_type) group. The property tests in
  `tests/test_invariants.py` are the guardrail — a red invariant test blocks merge.
- Anything that posts to the ledger or creates rows must be idempotent behind a key.
- All `/v1/` routes require an API key. Never log card numbers or PII; card tokens
  only.
- Schema changes go through Alembic (`make migrate-new`, then review the generated
  migration); run `alembic check` before committing to catch model drift.
- Keep the model and its feature schema in sync: bump `FEATURE_SCHEMA_VERSION` in
  `app/risk/features.py` when features change, retrain (`make train`), and commit the
  updated `metrics.json` and `docs/model-card.md`.

## Tests

- `tests/test_ledger.py` — double-entry balance and idempotency per posting function.
- `tests/test_authorization_api.py` / `tests/test_security.py` — API behavior, auth,
  rate limits, and the no-PII logging invariant.
- `tests/test_temporal.py` — Temporal workflows on the time-skipping test server.
- `tests/test_invariants.py` — property test over random operation sequences and the
  5,000-request replay storm.
- `tests/test_model.py` / `tests/test_features.py` — model serving and no-leakage
  feature checks.

## Reporting security issues

Do not open a public issue for vulnerabilities. Report privately to the maintainers;
include the affected version and a minimal reproduction if possible.
