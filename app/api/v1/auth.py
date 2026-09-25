from flask import Response, current_app, jsonify, request

from app.ratelimit import SlidingWindowLimiter


def _get_limiter() -> SlidingWindowLimiter:
    limiter = current_app.extensions.get("rate_limiter")
    if limiter is None:
        limiter = SlidingWindowLimiter(
            current_app.config["RATE_LIMIT_PER_KEY"],
            current_app.config["RATE_LIMIT_WINDOW_SECONDS"],
        )
        current_app.extensions["rate_limiter"] = limiter
    return limiter


def require_api_key() -> Response | tuple[Response, int] | None:
    key = request.headers.get("X-Api-Key")
    if not key or key not in current_app.config["API_KEYS"]:
        return jsonify({"error": "invalid or missing API key"}), 401
    limiter = _get_limiter()
    if not limiter.allow(key):
        response = jsonify({"error": "rate limit exceeded"})
        response.headers["Retry-After"] = str(max(int(limiter.retry_after(key)) + 1, 1))
        return response, 429
    return None
