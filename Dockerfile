# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_ENV=prod

# Set work directory
WORKDIR /app

# Install system dependencies.
# build-essential + python3-dev are required to build tgcrypto from source —
# TgCrypto 1.2.5 ships wheels only up to Python 3.11, so on 3.12 uv compiles it.
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    postgresql-client \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned to the version that generated uv.lock)
COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /uvx /bin/

# Copy the dependencies files
COPY pyproject.toml uv.lock ./

# Install dependencies (only production)
RUN uv sync --frozen --no-dev

# Copy project
COPY . .

# Collect static files
RUN uv run manage.py collectstatic --no-input

# Expose port
EXPOSE 8000

# Run gunicorn — worker count from GUNICORN_WORKERS env (default: 2 for modest servers)
ENV GUNICORN_WORKERS=2
CMD ["sh", "-c", "exec uv run gunicorn cooplink.wsgi:application --bind 0.0.0.0:8000 --workers \"$GUNICORN_WORKERS\" --timeout 120"]
