FROM python:3.13.9-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME="/home/app" \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.10.6 \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app app

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py ./
RUN mkdir -p /mnt/workspace /mnt/persistent \
    && chown -R app:app /app /home/app /mnt/workspace /mnt/persistent

USER app
EXPOSE 8080
CMD ["python", "main.py"]
