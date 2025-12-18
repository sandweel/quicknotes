FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y dumb-init \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

ENTRYPOINT ["dumb-init", "--"]

CMD gunicorn --bind 0.0.0.0:${FLASK_PORT:-5000} --workers ${GUNICORN_WORKERS:-1} app:app
