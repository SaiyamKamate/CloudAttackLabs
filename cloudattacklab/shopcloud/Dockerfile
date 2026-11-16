FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SHOP_DB_PATH=/app/data/shop.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shop ./shop

# Runs as a non-root user; only the database folder is writable by it.
RUN useradd --create-home --uid 10001 shop && mkdir -p /app/data && chown shop:shop /app/data
USER shop

EXPOSE 8000
# The database is created and seeded at start-up; no manual steps.
# --no-proxy-headers: source_ip in the logs is the real peer, never a client-supplied header.
CMD ["uvicorn", "--factory", "shop.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
