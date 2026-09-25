import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from workflows import HoldExpiryWorkflow, ReviewWorkflow
from workflows.activities import finalize_decline, post_review_hold, release_expired_hold

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cardguard.worker")


async def main():
    host = os.environ.get("TEMPORAL_HOST", "temporal:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("CARDGUARD_TASK_QUEUE", "cardguard-risk")

    client = await Client.connect(host, namespace=namespace)
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[ReviewWorkflow, HoldExpiryWorkflow],
        activities=[post_review_hold, finalize_decline, release_expired_hold],
    )
    logger.info("worker polling task queue %s on %s", task_queue, host)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
