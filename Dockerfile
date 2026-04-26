# copy source code
FROM alpine:3.22 AS code-layer
WORKDIR /usr/src

COPY src ./src
COPY etc/docker-entrypoint .

# export locked dependencies
FROM python:3.14-alpine AS requirements-layer
WORKDIR /usr/src
ARG DEV_DEPS="false"
ARG UV_VERSION=0.9.13

COPY pyproject.toml .
COPY uv.lock .

RUN pip install uv==${UV_VERSION} && \
    if [ "${DEV_DEPS}" = "true" ]; then \
      uv export --format requirements-txt --frozen --output-file requirements.txt; \
    else \
      uv export --format requirements-txt --frozen --no-dev --output-file requirements.txt; \
    fi

FROM python:3.14-alpine AS base
ARG PIP_DEFAULT_TIMEOUT=300
ARG PIP_VERSION=25.3
WORKDIR /app

COPY --from=requirements-layer /usr/src/requirements.txt .

RUN pip install --upgrade pip==${PIP_VERSION} && \
    pip install --timeout "${PIP_DEFAULT_TIMEOUT}" --no-cache-dir --require-hashes -r requirements.txt

RUN addgroup -S backgammon -g 1007 && \
    adduser -S -G backgammon -u 1007 -H backgammon && \
    mkdir -p /app/.data && \
    mkdir -p /app/staticfiles && \
    chown backgammon:backgammon /app/.data && \
    chown backgammon:backgammon /app/staticfiles

USER backgammon

COPY --from=code-layer --chown=backgammon:backgammon /usr/src .

RUN DJANGO_SECRET_KEY=build-time-static-key DJANGO_ALLOWED_HOSTS=localhost python src/manage.py collectstatic --noinput

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV APP_PORT=8000

FROM base AS service

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint"]

FROM base AS tests

COPY pyproject.toml .

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint"]
