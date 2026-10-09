FROM python:3.13

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Install system dependencies
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip
RUN pip install pipenv

WORKDIR /app

COPY Pipfile Pipfile.lock ./

# Install project dependencies
RUN pipenv install --system --dev

COPY . .

EXPOSE 8000

# Daphne serves Django's plain ASGI HTTP application; chat traffic uses Synapse.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "srisu.asgi:application"]
