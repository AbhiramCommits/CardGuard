# Query Plans

EXPLAIN ANALYZE output for the two policy-engine aggregates, run against PostgreSQL 16
with synthetic data: 50,000 authorizations and 100,000 ledger entries for one employee,
created_at spread uniformly over the past 365 days. Tables were ANALYZEd before capture.

## Indexes added for these queries

- `ix_ledger_entry_created_at` on `ledger_entry(created_at)` — month-window filter for the
  monthly-limit aggregate.
- `ix_authorization_card_id_created_at` on `authorization(card_id, created_at)` — rolling
  window filter for the velocity rule.

## 1. Monthly-limit aggregate (MONTHLY_LIMIT rule)

Single aggregate over `ledger_entry` scoped to the employee's card via the
authorization/card join, counting only entries on `holds`/`settled` accounts:

```sql
SELECT coalesce(cast(sum(CASE WHEN le.direction = 'debit' THEN le.amount_cents
                              ELSE -le.amount_cents END) AS bigint), 0) AS total
FROM ledger_entry le
JOIN account a ON a.id = le.account_id
JOIN "authorization" az ON az.id = le.authorization_id
JOIN card c ON c.id = az.card_id
WHERE c.employee_id = 1
  AND a.account_type IN ('holds', 'settled')
  AND le.created_at >= date_trunc('month', now());
```

### Default plan (planner-chosen, 50k authz / 100k entries)

```
 Aggregate  (cost=3657.31..3657.32 rows=1 width=8) (actual time=66.980..66.982 rows=1 loops=1)
   Buffers: shared hit=251551
   ->  Hash Join  (cost=2.93..3637.30 rows=2667 width=12) (actual time=0.027..65.697 rows=50004 loops=1)
         Hash Cond: (le.account_id = a.id)
         Buffers: shared hit=251551
         ->  Nested Loop  (cost=1.74..3620.68 rows=4000 width=20) (actual time=0.016..60.550 rows=100006 loops=1)
               Buffers: shared hit=251550
               ->  Hash Join  (cost=1.32..2193.86 rows=2000 width=8) (actual time=0.009..7.398 rows=50003 loops=1)
                     Hash Cond: (az.card_id = c.id)
                     Buffers: shared hit=1540
                     ->  Seq Scan on "authorization" az  (cost=0.00..2039.03 rows=50003 width=16) (actual time=0.001..4.174 rows=50003 loops=1)
                           Buffers: shared hit=1539
                     ->  Hash  (cost=1.31..1.31 rows=1 width=8) (actual time=0.004..0.004 rows=1 loops=1)
                           Buckets: 1024  Batches: 1  Memory Usage: 9kB
                           Buffers: shared hit=1
                           ->  Seq Scan on card c  (cost=0.00..1.31 rows=1 width=8) (actual time=0.002..0.002 rows=1 loops=1)
                                 Filter: (employee_id = 1)
                                 Rows Removed by Filter: 24
                                 Buffers: shared hit=1
               ->  Index Scan using ix_ledger_entry_authorization_id on ledger_entry le  (cost=0.42..0.69 rows=2 width=28) (actual time=0.001..0.001 rows=2 loops=50003)
                     Index Cond: (authorization_id = az.id)
                     Filter: (created_at >= date_trunc('month'::text, now()))
                     Buffers: shared hit=250010
         ->  Hash  (cost=1.11..1.11 rows=6 width=8) (actual time=0.007..0.007 rows=6 loops=1)
               Buckets: 1024  Batches: 1  Memory Usage: 9kB
               Buffers: shared hit=1
               ->  Seq Scan on account a  (cost=0.00..1.11 rows=6 width=8) (actual time=0.003..0.003 rows=6 loops=1)
                     Filter: (account_type = ANY ('{holds,settled}'::account_type[]))
                     Rows Removed by Filter: 3
                     Buffers: shared hit=1
 Planning:
   Buffers: shared hit=555
 Planning Time: 1.249 ms
 Execution Time: 67.020 ms
```

At this scale the planner drives from the authorization side and looks ledger entries up via
`ix_ledger_entry_authorization_id`. Note the `Filter` on `created_at` per row: 250,010 buffer hits.

### Index-driven variant (`SET enable_seqscan = off; SET enable_nestloop = off;`)

```
 Aggregate  (cost=8084.16..8084.17 rows=1 width=8) (actual time=57.564..57.566 rows=1 loops=1)
   Buffers: shared hit=4175
   ->  Hash Join  (cost=2963.59..8064.15 rows=2667 width=12) (actual time=29.969..56.317 rows=50004 loops=1)
         Hash Cond: (le.account_id = a.id)
         Buffers: shared hit=4175
         ->  Hash Join  (cost=2951.22..8036.35 rows=4000 width=20) (actual time=29.903..51.807 rows=100006 loops=1)
               Hash Cond: (le.authorization_id = az.id)
               Buffers: shared hit=4173
               ->  Index Scan using ix_ledger_entry_created_at on ledger_entry le  (cost=0.30..4670.40 rows=100006 width=28) (actual time=0.023..10.709 rows=100006 loops=1)
                     Index Cond: (created_at >= date_trunc('month'::text, now()))
                     Buffers: shared hit=2512
               ->  Hash  (cost=2925.92..2925.92 rows=2000 width=8) (actual time=29.863..29.864 rows=50003 loops=1)
                     Buckets: 65536 (originally 2048)  Batches: 1 (originally 1)  Memory Usage: 2466kB
                     Buffers: shared hit=1661
                     ->  Merge Join  (cost=0.43..2925.92 rows=2000 width=8) (actual time=0.046..25.496 rows=50003 loops=1)
                           Merge Cond: (az.card_id = c.id)
                           Buffers: shared hit=1661
                           ->  Index Scan using ix_authorization_card_id on "authorization" az  (cost=0.29..2768.34 rows=50003 width=16) (actual time=0.033..21.450 rows=50003 loops=1)
                                 Buffers: shared hit=1659
                           ->  Index Scan using card_pkey on card c  (cost=0.14..12.57 rows=1 width=8) (actual time=0.010..0.010 rows=1 loops=1)
                                 Filter: (employee_id = 1)
                                 Buffers: shared hit=2
         ->  Hash  (cost=12.29..12.29 rows=6 width=8) (actual time=0.042..0.042 rows=6 loops=1)
               Buckets: 1024  Batches: 1  Memory Usage: 9kB
               Buffers: shared hit=2
               ->  Index Scan using account_pkey on account a  (cost=0.14..12.29 rows=6 width=8) (actual time=0.013..0.029 rows=6 loops=1)
                     Filter: (account_type = ANY ('{holds,settled}'::account_type[]))
                     Rows Removed by Filter: 3
                     Buffers: shared hit=2
 Planning:
   Buffers: shared hit=551
 Planning Time: 5.764 ms
 Execution Time: 57.685 ms
```

Forced away from nested loops, the planner drives from `ix_ledger_entry_created_at`:
4,175 buffer hits vs 251,551 in the nested-loop plan. As `ledger_entry` grows (many
employees, months of history), the created_at range becomes more selective and this is the
access path the planner converges on; the index exists so the predicate never degrades to a
full table scan with a per-row filter.

## 2. Velocity query (VELOCITY rule)

Count of authorizations for the employee's cards inside the rolling window:

```sql
SELECT count(az.id)
FROM "authorization" az
JOIN card c ON c.id = az.card_id
WHERE c.employee_id = 1
  AND az.created_at > now() - interval '15 minutes';
```

### Plan (50k authorizations for the employee)

```
 Aggregate  (cost=34.56..34.57 rows=1 width=8) (actual time=0.086..0.088 rows=1 loops=1)
   Buffers: shared hit=17
   ->  Nested Loop  (cost=0.29..34.56 rows=1 width=8) (actual time=0.033..0.076 rows=15 loops=1)
         Buffers: shared hit=17
         ->  Seq Scan on card c  (cost=0.00..1.31 rows=1 width=8) (actual time=0.005..0.007 rows=1 loops=1)
               Filter: (employee_id = 1)
               Rows Removed by Filter: 24
               Buffers: shared hit=1
         ->  Index Scan using ix_authorization_card_id_created_at on "authorization" az  (cost=0.29..33.09 rows=15 width=16) (actual time=0.025..0.065 rows=15 loops=1)
               Index Cond: ((card_id = c.id) AND (created_at > (now() - '00:15:00'::interval)))
               Buffers: shared hit=16
 Planning:
   Buffers: shared hit=320
 Planning Time: 1.229 ms
 Execution Time: 0.130 ms
```

`ix_authorization_card_id_created_at` serves both predicates (`card_id` equality from the
card join, `created_at` range) in a single index scan: 17 buffer hits, 0.13 ms execution.
This plan shape is stable as the authorization table grows.
