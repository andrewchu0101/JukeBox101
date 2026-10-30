# Development-only test runner image. Never used for deployment; built and run explicitly by scripts/dev/run-tests.sh.
# Same pinned base as the runtime image, plus libpq for psycopg and the pinned test dependencies.
FROM python:3.12-slim-bookworm
ARG REQUIREMENTS_FILE=requirements-test.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY ${REQUIREMENTS_FILE} ./requirements-test-pinned.txt
RUN pip install --no-cache-dir -r requirements-test-pinned.txt
COPY . ./
RUN useradd --system --uid 10002 --home-dir /work --shell /usr/sbin/nologin jbtest && chown -R jbtest /work
USER 10002
ENTRYPOINT ["python", "-m", "pytest"]
