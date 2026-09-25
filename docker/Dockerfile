FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /srv/cardguard

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/srv/cardguard/.venv/bin:$PATH"

EXPOSE 8000

CMD ["gunicorn", "--workers", "4", "--threads", "8", "--bind", "0.0.0.0:8000", "wsgi:app"]
