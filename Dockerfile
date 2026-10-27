FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# tzdata so TZ (set in docker-compose.yml) applies to the daily run time.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cs2ts ./cs2ts
COPY config.example.json ./config.example.json

# data/ (SQLite) and config.json are mounted from the host; see docker-compose.yml.
CMD ["sh", "-c", "[ -f config.json ] || cp config.example.json config.json; exec python -m cs2ts serve --host 0.0.0.0 --port 8000 --daily-at ${DAILY_AT:-12:15}"]
