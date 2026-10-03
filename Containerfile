FROM docker.io/library/python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install runtime dependencies
COPY pyproject.toml /app/
RUN pip install --no-cache-dir \
    "argon2-cffi>=25.1,<26" \
    "flask>=3.1,<4" \
    "gunicorn>=23,<24" \
    "httpx>=0.28,<1" \
    "webauthn>=2.8,<3"

# Copy application source
COPY dashboard /app/dashboard

# Create unprivileged user and directories
RUN useradd -u 1000 -U -d /app -s /bin/sh app && \
    mkdir -p /data /run/eaf-dashboard && \
    chown -R app:app /app /data /run/eaf-dashboard

USER app

EXPOSE 8010

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8010", "dashboard.app:app"]
