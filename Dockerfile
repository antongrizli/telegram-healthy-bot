FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (required for Postgres compatibility or compiling) and apply security updates
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY src/ /app/src
COPY alembic.ini /app/
COPY migrations/ /app/migrations/

# Run application
CMD ["python", "-m", "src.main"]
