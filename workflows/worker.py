import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.service import TLSConfig
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    ExecuteWorkflowInput,
    Interceptor,
    Worker,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
)

from app.metrics import ACTIVITY_FAILURES, WORKFLOW_COMPLETIONS
from app.observability import install_json_logging
from workflows import HoldExpiryWorkflow, ReviewWorkflow
from workflows.activities import (
    finalize_decline,
    post_review_hold,
    release_expired_hold,
)

install_json_logging()
logger = logging.getLogger("cardguard.worker")


class MetricsInterceptor(Interceptor):
    def workflow_interceptor_class(
        self, input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor] | None:
        return WorkflowMetricsInterceptor

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return ActivityMetricsInterceptor(super().intercept_activity(next))


class WorkflowMetricsInterceptor(WorkflowInboundInterceptor):
    async def execute_workflow(self, input: ExecuteWorkflowInput) -> object:
        try:
            result = await super().execute_workflow(input)
        except Exception:
            WORKFLOW_COMPLETIONS.labels(
                workflow=input.workflow_type, result="failed"
            ).inc()
            raise
        WORKFLOW_COMPLETIONS.labels(
            workflow=input.workflow_type, result="completed"
        ).inc()
        return result


class ActivityMetricsInterceptor(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> object:
        try:
            return await super().execute_activity(input)
        except Exception as exc:
            ACTIVITY_FAILURES.labels(
                activity=input.activity_type, error=type(exc).__name__
            ).inc()
            raise


def _temporal_tls() -> TLSConfig | None:
    cert_path = os.environ.get("TEMPORAL_CLIENT_CERT")
    key_path = os.environ.get("TEMPORAL_CLIENT_KEY")
    if not cert_path or not key_path:
        return None
    with open(cert_path, "rb") as cert_file, open(key_path, "rb") as key_file:
        return TLSConfig(
            client_cert=cert_file.read(),
            client_private_key=key_file.read(),
        )


async def main() -> None:
    host = os.environ.get("TEMPORAL_HOST", "temporal:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("CARDGUARD_TASK_QUEUE", "cardguard-risk")

    client = await Client.connect(host, namespace=namespace, tls=_temporal_tls())
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[ReviewWorkflow, HoldExpiryWorkflow],
        activities=[post_review_hold, finalize_decline, release_expired_hold],
        interceptors=[MetricsInterceptor()],
    )
    logger.info("worker polling task queue %s on %s", task_queue, host)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
