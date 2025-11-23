FROM python:3.12.1

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONBUFFERED=1

RUN apt-get update
RUN apt-get -y install postgresql-client

RUN pip install --upgrade pip
RUN pip install pipenv

WORKDIR /app

COPY Pipfile .
COPY Pipfile.lock .

RUN pipenv install --system --dev

COPY . /app/

EXPOSE 8000

CMD ["python","manage.py","runserver","0.0.0.0:8000"]
