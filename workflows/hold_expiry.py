import asyncio

from temporalio import workflow
from temporalio.common import VersioningBehavior

from workflows.activities import (
    ACTIVITY_RETRY_POLICY,
    ACTIVITY_START_TO_CLOSE_TIMEOUT,
    release_expired_hold,
)


@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class HoldExpiryWorkflow:
    @workflow.run
    async def run(self, authorization_id: int, expires_at_epoch: float) -> str:
        delay = expires_at_epoch - workflow.now().timestamp()
        if delay > 0:
            await asyncio.sleep(delay)
        return await workflow.execute_activity(
            release_expired_hold,
            args=[authorization_id],
            start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
            retry_policy=ACTIVITY_RETRY_POLICY,
        )
