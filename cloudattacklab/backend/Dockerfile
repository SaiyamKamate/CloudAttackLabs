FROM python:3.12-slim

# The Docker CLI and its compose/buildx plugins only. The Docker daemon is the
# host's, reached through the mounted socket (see docker-compose.yml).
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:29-cli /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY alembic alembic
COPY app app

# Runs as root so it can use the mounted Docker socket.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host ${BACKEND_HOST:-127.0.0.1} --port ${BACKEND_PORT:-8000}"]
