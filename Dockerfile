# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV DJANGO_ENV prod

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

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
CMD uv run gunicorn cooplink.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS} --timeout 120
