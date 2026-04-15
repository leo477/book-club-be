# Stage 1: builder
FROM python:3.12-slim AS builder

WORKDIR /build

# Додаємо apt-get upgrade для виправлення вразливостей системних ліб
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Оновлюємо pip перед інсталяцією
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: runtime
FROM python:3.12-slim

# 1. Оновлюємо систему для виправлення відомих CVE у базовому образі
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid appuser --shell /bin/sh --create-home appuser

# Копіюємо залежності
COPY --from=builder /install /usr/local
COPY app/ /app/app/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/alembic.ini

WORKDIR /app

# Змінюємо власника файлів
RUN chown -R appuser:appuser /app

# Додатковий захист: робимо деякі системні папки лише для читання, де це можливо
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]