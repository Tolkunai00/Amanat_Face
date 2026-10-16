FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Bishkek \
    HOME=/home/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential g++ libgl1 libglib2.0-0 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential g++

COPY backend/ ./
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data /app/face_database /app/screenshots /app/logs /app/tmp /home/app/.insightface \
    && chown -R app:app /app /home/app

USER app
EXPOSE 8080
CMD ["uvicorn", "school_face_id.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
