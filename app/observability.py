import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

BUDGET_MS = 250.0

logger = logging.getLogger("cardguard")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED = {
    "msg",
    "message",
    "name",
    "levelname",
    "asctime",
    "msecs",
    "process",
    "thread",
    "request_id",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        message = record.getMessage()
        if message.strip().startswith("{") and message.strip().endswith("}"):
            try:
                payload["event"] = json.loads(message)
            except ValueError:
                payload["msg"] = message
        else:
            payload["msg"] = message
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


def install_json_logging() -> None:
    root = logging.getLogger()
    root.handlers = [
        handler for handler in root.handlers if not isinstance(handler, _JsonHandler)
    ]
    handler = _JsonHandler()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class _JsonHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            print(self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)


def get_request_id() -> str:
    request_id = request_id_var.get()
    if request_id is None:
        request_id = uuid4().hex
        request_id_var.set(request_id)
    return request_id


@contextmanager
def request_scope(request_id: str) -> Iterator[None]:
    token = request_id_var.set(request_id)
    try:
        yield
    finally:
        request_id_var.reset(token)


class StageTimer:
    def __init__(self, event: str) -> None:
        self.event = event
        self._start = time.perf_counter()
        self._stages: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self._stages[name] = round((time.perf_counter() - start) * 1000, 3)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000

    def finish(self) -> None:
        total_ms = round(self.elapsed_ms(), 3)
        event = {
            "event": self.event,
            "latency_ms": total_ms,
            "budget_ms": BUDGET_MS,
            "over_budget": total_ms > BUDGET_MS,
            "stages": self._stages,
        }
        message = json.dumps(event, sort_keys=True, default=str)
        if total_ms > BUDGET_MS:
            logger.warning(message)
        else:
            logger.info(message)
