FROM python:3.13.5

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Install system dependencies
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*


WORKDIR /app

COPY Pipfile Pipfile.lock requirements-core-tests.txt ./

# Install project dependencies
RUN python -m venv /tmp/pipenv-build \
    && /tmp/pipenv-build/bin/pip install --no-cache-dir pipenv==2026.9.1 \
    && /tmp/pipenv-build/bin/pipenv requirements --dev --hash > /tmp/requirements.lock \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && pip install --no-cache-dir -r requirements-core-tests.txt \
    && rm -rf /tmp/pipenv-build /tmp/requirements.lock

COPY . .

EXPOSE 8000

# Daphne serves Django's plain ASGI HTTP application; chat traffic uses Synapse.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "srisu.asgi:application"]

# Additive provider dependencies, separate from build tooling.
RUN pip install --no-deps -r requirements-notifications.txt && pip check
