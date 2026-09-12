FROM python:3.12-alpine

WORKDIR /app

# Copy dependency definition
COPY requirements.txt .

# Install runtime dependencies, temporary build tools, compile packages, and clean up in one layer.
# pip is build-only and is removed because its bundled msgpack would otherwise ship in the image.
RUN apk update && apk upgrade && apk add --no-cache \
    postgresql-libs \
    libpng \
    freetype \
    libstdc++ \
    && apk add --no-cache --virtual .build-deps \
    build-base \
    postgresql-dev \
    freetype-dev \
    libpng-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -rf /usr/local/lib/python3.12/site-packages/pip \
    && apk del .build-deps \
    && rm -rf /var/cache/apk/*

# Copy application source code
COPY src/ /app/src
COPY alembic.ini /app/
COPY migrations/ /app/migrations/

# Run application
CMD ["python", "-m", "src.main"]
