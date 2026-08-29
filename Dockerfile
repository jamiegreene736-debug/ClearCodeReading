FROM node:22-alpine AS frontend

WORKDIR /app

COPY package.json package-lock.json tailwind.config.js ./
RUN npm ci
COPY assets/styles ./assets/styles
COPY marketing-website ./marketing-website
COPY templates ./templates
COPY apps ./apps
RUN npm run build:css

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .
COPY --from=frontend /app/apps/core/static/css/clearcode-tailwind.css /app/apps/core/static/css/clearcode-tailwind.css
RUN python manage.py collectstatic --noinput \
    && chmod +x /app/scripts/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["sh", "-c", "gunicorn clearcodereading.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3}"]
