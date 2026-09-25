import json
import logging
import time
from contextlib import contextmanager

BUDGET_MS = 250.0

logger = logging.getLogger("cardguard")


class StageTimer:
    def __init__(self, event):
        self.event = event
        self._start = time.perf_counter()
        self._stages = {}

    @contextmanager
    def stage(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self._stages[name] = round((time.perf_counter() - start) * 1000, 3)

    def elapsed_ms(self):
        return (time.perf_counter() - self._start) * 1000

    def finish(self):
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
