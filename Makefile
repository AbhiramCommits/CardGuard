.PHONY: up down logs migrate migrate-new seed test data train demo demo-crash loadtest psql

m ?= auto

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose run --rm api alembic upgrade head

migrate-new:
	docker compose run --rm api alembic revision --autogenerate -m "$(m)"

seed:
	docker compose run --rm api python scripts/seed.py

test:
	docker compose up -d postgres
	uv run pytest --cov=app --cov-report=term-missing

data:
	uv run python ml/generate_synthetic.py

train:
	uv run python ml/train.py

demo:
	bash scripts/demo.sh

demo-crash:
	bash scripts/demo_crash_recovery.sh

loadtest:
	uv run python loadtest/setup.py
	k6 run loadtest/scenario_a.js
	uv run python loadtest/summarize.py scenario_a
	uv run python loadtest/setup.py
	k6 run loadtest/scenario_b.js
	uv run python loadtest/summarize.py scenario_b

psql:
	docker compose exec postgres psql -U cardguard -d cardguard
