#!/usr/bin/env bash
set -euo pipefail

API="http://localhost:8000"
KEY="dev-key"
ledger_state() {
  docker compose exec -T postgres psql -U cardguard -d cardguard <<'SQL'
SELECT a.account_type,
       coalesce(sum(CASE WHEN e.direction = 'debit' THEN e.amount_cents ELSE -e.amount_cents END), 0) AS balance_cents
  FROM account a
  LEFT JOIN ledger_entry e ON e.account_id = a.id
 WHERE a.company_id = 1
 GROUP BY a.account_type ORDER BY a.account_type;
SQL
}

authz() {
  local amount="$1" mcc="$2"
  curl -sf -X POST "$API/v1/authorizations" \
    -H 'Content-Type: application/json' -H "X-Api-Key: $KEY" \
    -d "{\"idempotency_key\":\"demo-$(date +%s%N)\",\"card_token\":\"$TOKEN\",\"merchant_name\":\"DEMO MERCHANT\",\"mcc\":\"$mcc\",\"amount_cents\":$amount,\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"
}

echo "==> starting the stack"
docker compose up -d --build
docker compose run --rm api alembic upgrade head

if [ "$(docker compose exec -T postgres psql -U cardguard -d cardguard -tAc "SELECT count(*) FROM company")" = "0" ]; then
  echo "==> seeding"
  docker compose run --rm api python scripts/seed.py
fi

echo "==> deterministic policies for the demo card"
docker compose exec -T postgres psql -U cardguard -d cardguard <<'SQL'
UPDATE spend_policy
   SET monthly_limit_cents = 100000, per_transaction_limit_cents = 50000,
       blocked_mccs = '{"5541"}', velocity_max_auths = 100000
 WHERE employee_id = 1;
DELETE FROM ledger_entry WHERE authorization_id IN (SELECT id FROM "authorization" WHERE card_id = 1);
DELETE FROM ledger_posting WHERE authorization_id IN (SELECT id FROM "authorization" WHERE card_id = 1);
DELETE FROM "authorization" WHERE card_id = 1;
SQL

TOKEN=$(docker compose exec -T postgres psql -U cardguard -d cardguard -tAc "SELECT token FROM card WHERE id = 1")

echo
echo "==> 1. APPROVE: \$30 at an allowed MCC"
RESP=$(authz 3000 5411)
echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('decision:', d['decision'], '-', d['decision_reason'])"
AID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['authorization_id'])")
echo "ledger state (hold posted):"
ledger_state

echo
echo "==> 2. DECLINE on policy: \$20 at blocked MCC 5541"
authz 2000 5541 | python3 -c "import sys,json; d=json.load(sys.stdin); print('decision:', d['decision'], '-', d['decision_reason'])"
echo "ledger state (unchanged):"
ledger_state

echo
echo "==> 3. REVIEW: shrink the monthly limit so the next transaction is near-limit"
docker compose exec -T postgres psql -U cardguard -d cardguard \
  -c "UPDATE spend_policy SET monthly_limit_cents = 4000, per_transaction_limit_cents = 50000 WHERE employee_id = 1" > /dev/null
RESP=$(authz 3500 5411)
echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('decision:', d['decision'], '-', d['decision_reason'])"
RID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['authorization_id'])")
echo "review workflow state:"
curl -sf -H "X-Api-Key: $KEY" "$API/v1/authorizations/$RID/review-status"; echo
echo "reviewer approves via signal:"
curl -sf -X POST -H 'Content-Type: application/json' -H "X-Api-Key: $KEY" \
  "$API/v1/authorizations/$RID/review-decision" \
  -d '{"decision":"approve","reviewer_id":"demo-reviewer","note":"looks fine"}' > /dev/null
for _ in $(seq 1 30); do
  STATE=$(curl -sf -H "X-Api-Key: $KEY" "$API/v1/authorizations/$RID/review-status")
  echo "$STATE" | grep -q '"approved"' && break
  sleep 1
done
echo "review workflow state: $STATE"
echo "ledger state (review hold posted):"
ledger_state

echo
echo "==> 4. CAPTURE the first authorization"
curl -sf -X POST -H 'Content-Type: application/json' -H "X-Api-Key: $KEY" \
  "$API/v1/authorizations/$AID/capture" -d '{"amount_cents":3000}'; echo
echo "ledger state (money settled):"
ledger_state

echo
echo "==> 5. HOLD EXPIRY: approve \$10, then let the durable timer release it"
RESP=$(authz 1000 5411)
EID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['authorization_id'])")
EID_INT=$(docker compose exec -T postgres psql -U cardguard -d cardguard -tAc "SELECT id FROM \"authorization\" WHERE public_id = '$EID'")
docker compose run --rm -T api python - "$EID_INT" "$EID" <<'PYEOF'
import sys
import time

from app import create_app
from app.temporal import start_hold_expiry_workflow

create_app()
print(
    "expiry workflow started:",
    start_hold_expiry_workflow(int(sys.argv[1]), sys.argv[2], time.time() + 5, "demo"),
)
PYEOF
echo "waiting for the durable timer..."
sleep 10
docker compose exec -T postgres psql -U cardguard -d cardguard \
  -c "SELECT status, decision_reason FROM \"authorization\" WHERE public_id = '$EID';"
echo "ledger state (hold released):"
ledger_state

echo
echo "==> done: approve -> policy decline -> review+signal -> capture -> expiry release"
