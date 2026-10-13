FROM python:3.13.5

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Install system dependencies
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY Pipfile Pipfile.lock ./

# Keep build-tool dependencies separate from the application's locked packages.
# In particular, newer virtualenv releases require a newer packaging than our lock.
RUN python -m venv /tmp/pipenv-build \
    && /tmp/pipenv-build/bin/pip install --no-cache-dir pipenv==2026.9.1 \
    && /tmp/pipenv-build/bin/pipenv requirements --dev --hash > /tmp/requirements.lock \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && rm -rf /tmp/pipenv-build /tmp/requirements.lock

COPY . .

EXPOSE 8000

# Use daphne for ASGI (WebSockets) support
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "srisu.asgi:application"]

# Additive, pinned push-provider dependencies. Existing Pipfile packages stay locked.
RUN pip install --no-deps -r requirements-notifications.txt && pip check
