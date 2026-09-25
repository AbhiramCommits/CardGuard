import asyncio
import concurrent.futures
import logging
import os
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict
from typing import Any

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from workflows import HoldExpiryWorkflow, ReviewWorkflow

logger = logging.getLogger("cardguard")

TASK_QUEUE = os.environ.get("CARDGUARD_TASK_QUEUE", "cardguard-risk")
DEFAULT_TEMPORAL_HOST = "localhost:7233"
DEFAULT_TEMPORAL_NAMESPACE = "default"


class TemporalUnavailableError(RuntimeError):
    pass


class WorkflowNotFoundError(LookupError):
    pass


_client: Client | None = None
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_last_connect_attempt = 0.0
_last_connect_host: str | None = None
CONNECT_RETRY_COOLDOWN_SECONDS = 30.0


def _ensure_client(timeout: float = 5.0) -> Client:
    global _client, _loop, _last_connect_attempt, _last_connect_host
    host = os.environ.get("TEMPORAL_HOST", DEFAULT_TEMPORAL_HOST)
    namespace = os.environ.get("TEMPORAL_NAMESPACE", DEFAULT_TEMPORAL_NAMESPACE)
    with _lock:
        if _client is not None:
            return _client
        now = time.monotonic()
        if host != _last_connect_host:
            _last_connect_host = host
            _last_connect_attempt = 0.0
        if (
            _last_connect_attempt
            and now - _last_connect_attempt < CONNECT_RETRY_COOLDOWN_SECONDS
        ):
            raise TemporalUnavailableError(f"temporal unavailable at {host} (cooldown)")
        _last_connect_attempt = now
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(
                target=_loop.run_forever, daemon=True, name="cardguard-temporal"
            ).start()
        future: concurrent.futures.Future[Client] = asyncio.run_coroutine_threadsafe(
            Client.connect(host, namespace=namespace), _loop
        )
        try:
            _client = future.result(timeout=timeout)
            logger.info("connected to Temporal at %s (namespace %s)", host, namespace)
        except Exception as exc:
            logger.error("failed to connect to Temporal at %s: %s", host, exc)
            raise TemporalUnavailableError(f"temporal unavailable at {host}") from exc
    return _client


def _call(
    coro_factory: Callable[[Client], Coroutine[Any, Any, Any]], timeout: float = 10.0
) -> Any:
    client = _ensure_client()
    loop = _loop
    assert loop is not None
    future = asyncio.run_coroutine_threadsafe(coro_factory(client), loop)
    try:
        return future.result(timeout=timeout)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise WorkflowNotFoundError(str(exc)) from exc
        raise TemporalUnavailableError(str(exc)) from exc


def start_review_workflow(
    authorization_id: int, public_id: str, timeout_seconds: int
) -> bool:
    try:
        _call(
            lambda client: client.start_workflow(
                ReviewWorkflow.run,
                args=[authorization_id, timeout_seconds],
                id=f"review-{public_id}",
                task_queue=TASK_QUEUE,
            )
        )
        return True
    except WorkflowAlreadyStartedError:
        return True
    except Exception:
        logger.exception(
            "failed to start review workflow for authorization %s (public %s)",
            authorization_id,
            public_id,
        )
        return False


def start_hold_expiry_workflow(
    authorization_id: int, public_id: str, expires_at_epoch: float
) -> bool:
    try:
        _call(
            lambda client: client.start_workflow(
                HoldExpiryWorkflow.run,
                args=[authorization_id, expires_at_epoch],
                id=f"hold-expiry-{public_id}",
                task_queue=TASK_QUEUE,
            )
        )
        return True
    except WorkflowAlreadyStartedError:
        return True
    except Exception:
        logger.exception(
            "failed to start hold-expiry workflow for authorization %s (public %s)",
            authorization_id,
            public_id,
        )
        return False


def signal_review_decision(
    public_id: str, approve: bool, reviewer_id: str, note: str
) -> None:
    _call(
        lambda client: client.get_workflow_handle(f"review-{public_id}").signal(
            ReviewWorkflow.reviewer_decision, args=[approve, reviewer_id, note]
        )
    )


def query_review_state(public_id: str) -> dict[str, Any]:
    state = _call(
        lambda client: client.get_workflow_handle(f"review-{public_id}").query(
            ReviewWorkflow.get_state
        )
    )
    return asdict(state)
