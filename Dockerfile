FROM python:3.12-alpine

WORKDIR /app

# Install runtime dependencies and compilation tools for native package build
RUN apk update && apk add --no-cache \
    postgresql-libs \
    libpng \
    freetype \
    libstdc++ \
    && apk add --no-cache --virtual .build-deps \
    build-base \
    postgresql-dev \
    freetype-dev \
    libpng-dev \
    python3-dev

# Copy dependency definition
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Clean up build dependencies
RUN apk del .build-deps

# Copy application source code
COPY src/ /app/src
COPY alembic.ini /app/
COPY migrations/ /app/migrations/

# Run application
CMD ["python", "-m", "src.main"]
