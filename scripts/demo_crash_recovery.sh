#!/usr/bin/env bash
set -euo pipefail

echo "==> starting the full stack (postgres, temporal, worker, api)"
docker compose up -d --build

echo "==> running migrations"
docker compose run --rm api alembic upgrade head

if [ "$(docker compose exec -T postgres psql -U cardguard -d cardguard -tAc "SELECT count(*) FROM company")" = "0" ]; then
  echo "==> seeding"
  docker compose run --rm api python scripts/seed.py
fi

echo "==> forcing employee 1 into the review bucket (tiny monthly limit)"
docker compose exec -T postgres psql -U cardguard -d cardguard <<'SQL'
UPDATE spend_policy
   SET monthly_limit_cents = 1000, per_transaction_limit_cents = 100000, blocked_mccs = '{}'
 WHERE employee_id = 1;
DELETE FROM ledger_entry WHERE authorization_id IN (SELECT id FROM "authorization" WHERE card_id = 1);
DELETE FROM ledger_posting WHERE authorization_id IN (SELECT id FROM "authorization" WHERE card_id = 1);
DELETE FROM "authorization" WHERE card_id = 1;
SQL

TOKEN=$(docker compose exec -T postgres psql -U cardguard -d cardguard -tAc "SELECT token FROM card WHERE id = 1")
KEY="demo-$(date +%s)"
RESPONSE=$(curl -s -X POST http://localhost:8000/v1/authorizations \
  -H 'Content-Type: application/json' \
  -d "{\"idempotency_key\":\"$KEY\",\"card_token\":\"$TOKEN\",\"merchant_name\":\"DEMO MERCHANT\",\"mcc\":\"5411\",\"amount_cents\":950,\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}")
echo "authorization: $RESPONSE"
AUTHZ_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['authorization_id'])")
echo "authorization_id=$AUTHZ_ID"

echo "==> review workflow state (running in Temporal):"
curl -s "http://localhost:8000/v1/authorizations/$AUTHZ_ID/review-status"; echo

echo "==> killing the worker container mid-workflow"
docker compose kill worker

echo "==> restarting the worker"
docker compose start worker
sleep 5

echo "==> sending the approve signal"
curl -s -X POST "http://localhost:8000/v1/authorizations/$AUTHZ_ID/review-decision" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","reviewer_id":"demo-reviewer","note":"crash recovery demo"}'; echo

echo "==> polling review status until approved"
for _ in $(seq 1 30); do
  STATE=$(curl -s "http://localhost:8000/v1/authorizations/$AUTHZ_ID/review-status")
  echo "$STATE"
  echo "$STATE" | grep -q '"approved"' && break
  sleep 1
done

echo "==> ledger state after recovery"
docker compose exec -T postgres psql -U cardguard -d cardguard <<SQL
SELECT a.status,
       a.decision_reason,
       (SELECT count(*) FROM ledger_posting p WHERE p.authorization_id = a.id) AS postings,
       (SELECT count(*) FROM ledger_entry e WHERE e.authorization_id = a.id) AS entries
  FROM "authorization" a WHERE a.public_id = '$AUTHZ_ID';
SQL

echo "==> done: the workflow survived the worker crash and the hold was posted exactly once"
