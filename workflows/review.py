import asyncio
from dataclasses import dataclass, field

from temporalio import workflow
from temporalio.common import VersioningBehavior

from workflows.activities import (
    ACTIVITY_RETRY_POLICY,
    ACTIVITY_START_TO_CLOSE_TIMEOUT,
    finalize_decline,
    post_review_hold,
)

DEFAULT_REVIEW_TIMEOUT_SECONDS = 24 * 3600


@dataclass
class ReviewState:
    status: str = "pending_review"
    decision: str | None = None
    reviewer_id: str | None = None
    note: str | None = None


@dataclass
class _Decision:
    approve: bool = field(default=True)
    reviewer_id: str = ""
    note: str = ""


@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class ReviewWorkflow:
    def __init__(self):
        self._decision: _Decision | None = None
        self._state = ReviewState()

    @workflow.run
    async def run(
        self,
        authorization_id: int,
        timeout_seconds: int = DEFAULT_REVIEW_TIMEOUT_SECONDS,
        request_id: str = "",
    ) -> str:
        try:
            async with asyncio.timeout(timeout_seconds):
                await workflow.wait_condition(lambda: self._decision is not None)
        except TimeoutError:
            self._state.status = "auto_declined"
            self._state.decision = "timeout"
            await workflow.execute_activity(
                finalize_decline,
                args=[authorization_id, "REVIEW_TIMEOUT", None, None, request_id],
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            return "REVIEW_TIMEOUT"

        decision = self._decision
        self._state.reviewer_id = decision.reviewer_id
        self._state.note = decision.note
        if decision.approve:
            self._state.status = "approved"
            self._state.decision = "approve"
            await workflow.execute_activity(
                post_review_hold,
                args=[authorization_id, request_id],
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            return "APPROVED"

        self._state.status = "declined"
        self._state.decision = "decline"
        await workflow.execute_activity(
            finalize_decline,
            args=[
                authorization_id,
                "REVIEW_DECLINED",
                decision.reviewer_id,
                decision.note,
                request_id,
            ],
            start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
        return "DECLINED"

    @workflow.signal
    async def reviewer_decision(
        self, approve: bool, reviewer_id: str, note: str
    ) -> None:
        self._decision = _Decision(approve=approve, reviewer_id=reviewer_id, note=note)

    @workflow.query
    def get_state(self) -> ReviewState:
        return self._state
