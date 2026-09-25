from dataclasses import dataclass
from enum import Enum


class ReasonCode(str, Enum):
    APPROVED = "APPROVED"
    MCC_BLOCKED = "MCC_BLOCKED"
    PER_TXN_LIMIT = "PER_TXN_LIMIT"
    MONTHLY_LIMIT = "MONTHLY_LIMIT"
    VELOCITY = "VELOCITY"
    NEAR_MONTHLY_LIMIT = "NEAR_MONTHLY_LIMIT"


REVIEW_THRESHOLD = 0.9


@dataclass(frozen=True)
class PolicyContext:
    mcc: str
    amount_cents: int
    blocked_mccs: tuple[str, ...]
    per_transaction_limit_cents: int
    monthly_limit_cents: int
    monthly_used_cents: int
    velocity_count: int
    velocity_max_auths: int


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reason_code: ReasonCode
    risk_score: float


def evaluate_policy(ctx: PolicyContext) -> PolicyDecision:
    if ctx.mcc in ctx.blocked_mccs:
        return PolicyDecision("decline", ReasonCode.MCC_BLOCKED, 100.0)
    if ctx.amount_cents > ctx.per_transaction_limit_cents:
        return PolicyDecision("decline", ReasonCode.PER_TXN_LIMIT, 100.0)
    projected = ctx.monthly_used_cents + ctx.amount_cents
    if ctx.monthly_limit_cents and projected > ctx.monthly_limit_cents:
        return PolicyDecision("decline", ReasonCode.MONTHLY_LIMIT, 100.0)
    if ctx.velocity_count >= ctx.velocity_max_auths:
        return PolicyDecision("decline", ReasonCode.VELOCITY, 100.0)
    utilization = projected / ctx.monthly_limit_cents if ctx.monthly_limit_cents else 1.0
    if utilization >= REVIEW_THRESHOLD:
        return PolicyDecision(
            "review", ReasonCode.NEAR_MONTHLY_LIMIT, round(utilization * 100, 2)
        )
    return PolicyDecision("approve", ReasonCode.APPROVED, round(utilization * 100, 2))
