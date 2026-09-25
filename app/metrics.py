from prometheus_client import Counter, Histogram

AUTH_DECISIONS = Counter(
    "cardguard_auth_decisions_total",
    "Authorization decisions",
    ["decision", "reason"],
)

AUTH_LATENCY = Histogram(
    "cardguard_auth_latency_seconds",
    "Authorization decision latency",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

LEDGER_POSTINGS = Counter(
    "cardguard_ledger_postings_total",
    "Ledger postings",
    ["entry_type"],
)

MODEL_SCORE = Histogram(
    "cardguard_model_score",
    "Model fraud probability for scored authorizations",
    buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 0.63, 0.72, 0.9, 0.99),
)

WORKFLOW_STARTS = Counter(
    "cardguard_workflow_starts_total",
    "Workflows started by the API",
    ["workflow"],
)

WORKFLOW_COMPLETIONS = Counter(
    "cardguard_workflow_completions_total",
    "Workflows completed",
    ["workflow", "result"],
)

ACTIVITY_FAILURES = Counter(
    "cardguard_activity_failures_total",
    "Activity failures",
    ["activity", "error"],
)
