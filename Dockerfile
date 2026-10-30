# Runtime image for the multi-guild Gateway Bot on PostgreSQL. Secrets are never build arguments; they are mounted at run time.
# Build stage installs pinned runtime dependencies into a prefix; the final stage keeps no compiler or pip cache.
FROM python:3.12-slim-bookworm AS build
WORKDIR /build
COPY requirements-docker.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements-docker.txt

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC
# libpq5 (Debian bookworm) is the C client library psycopg uses; ca-certificates is already in the base image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 jukebox \
 && useradd --system --uid 10001 --gid jukebox --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin jukebox
COPY --from=build /install /usr/local
WORKDIR /app
COPY bot ./bot
USER 10001:10001
ENTRYPOINT ["python", "-m", "bot"]
