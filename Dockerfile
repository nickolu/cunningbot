FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip

# Create non-root user with UID 1000 (typical first user on Raspberry Pi)
RUN adduser --disabled-password --uid 1000 appuser

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code and give ownership in one shot
COPY --chown=appuser:appuser . .

# Ensure app_state.json and logs directory exist with proper permissions
RUN mkdir -p /app/bot/core && \
    mkdir -p /app/logs && \
    touch /app/bot/core/app_state.json && \
    chown -R appuser:appuser /app/bot/core && \
    chown -R appuser:appuser /app/logs && \
    chmod 644 /app/bot/core/app_state.json && \
    chmod 755 /app/logs

USER appuser

# Run the bot
CMD ["python", "-m", "bot.main"]
