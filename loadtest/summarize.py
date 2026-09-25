import json
import re
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import Config
from app.db import make_engine, make_session_factory
from app.models import Authorization

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _parse_k6_log(path):
    text = Path(path).read_text()
    duration_match = re.search(
        r"http_req_duration[.:\s]+avg=([\d.]+)(m?s)\s+min=([\d.]+)(m?s)\s+med=([\d.]+)(m?s)\s+max=([\d.]+)(m?s)\s+p\(90\)=([\d.]+)(m?s)\s+p\(95\)=([\d.]+)(m?s)\s+p\(99\)=([\d.]+)(m?s)",
        text,
    )
    requests_match = re.search(r"http_reqs[.:\s]+(\d+)\s+([\d.]+)/s", text)
    failed_match = re.search(
        r"http_req_failed[.:\s]+([\d.]+)%\s+(\d+) out of (\d+)", text
    )
    replays_match = re.search(r"idempotent_replays[.:\s]+(\d+)\s+([\d.]+)/s", text)
    dropped_match = re.search(r"dropped_iterations[.:\s]+(\d+)", text)

    if not duration_match or not requests_match or not failed_match:
        raise RuntimeError(f"could not parse k6 summary from {path}")

    def _ms(value, unit):
        return float(value) * (1000.0 if unit == "s" else 1.0)

    groups = duration_match.groups()
    return {
        "latency_ms": {
            "p50": _ms(groups[4], groups[5]),
            "p95": _ms(groups[10], groups[11]),
            "p99": _ms(groups[12], groups[13]),
            "max": _ms(groups[6], groups[7]),
        },
        "requests": int(requests_match.group(1)),
        "rps": float(requests_match.group(2)),
        "error_rate": float(failed_match.group(1)) / 100,
        "failed": int(failed_match.group(2)),
        "replays": int(replays_match.group(1)) if replays_match else None,
        "dropped_iterations": int(dropped_match.group(1)) if dropped_match else 0,
    }


def _decision_mix():
    engine = make_engine(Config.DATABASE_URL)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        rows = session.execute(
            select(Authorization.status, func.count())
            .where(Authorization.card_id.in_([1, 2, 3]))
            .group_by(Authorization.status)
        ).all()
    return {status.value: count for status, count in rows}


def summarize(scenario, log_path, extra=None):
    stats = _parse_k6_log(log_path)
    result = {
        "scenario": scenario,
        **stats,
        "decision_mix": _decision_mix() if scenario == "scenario_a" else None,
    }
    if extra:
        result.update(extra)
    out = RESULTS_DIR / f"{scenario}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {out}")
    return result


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "scenario_a"
    descriptions = {
        "scenario_a": "Steady-state mixed approve/decline/review, ramp to 50 rps sustained "
        "for 60s. Stack: gunicorn 4 workers x 8 threads, PostgreSQL 16, "
        "LightGBM scoring in-process.",
        "scenario_b": "Retry storm: 20% of requests replay a previously used idempotency "
        "key. Same ramp profile as scenario A.",
    }
    result = summarize(
        scenario,
        RESULTS_DIR / f"{scenario}_run.log",
        {"description": descriptions[scenario]},
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
