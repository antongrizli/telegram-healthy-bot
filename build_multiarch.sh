#!/usr/bin/env bash
set -e

IMAGE_NAME="ghcr.io/antongrizli/telegram-heathy-bot"
TAG="latest"

echo "Building Docker image locally..."
docker build -t "${IMAGE_NAME}:${TAG}" .

echo "Running test suite inside the built image..."
docker run --rm \
  -v "$(pwd)/tests":/app/tests:ro \
  -w /app \
  -e PYTHONPATH=. \
  -e TELEGRAM_BOT_TOKEN="00000000:test_token" \
  -e GEMINI_API_KEY="test_key" \
  -e DATABASE_URL="sqlite+aiosqlite:///:memory:" \
  -e WEBAPP_URL="https://localhost" \
  "${IMAGE_NAME}:${TAG}" pytest -v

echo "Analyzing vulnerabilities with Docker Scout..."
docker scout cves "${IMAGE_NAME}:${TAG}"

echo "Starting database and bot container using docker-compose..."
docker-compose up --build -d

echo "Bot container built, tested, and running locally!"
