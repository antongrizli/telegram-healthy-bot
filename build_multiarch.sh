#!/usr/bin/env bash
set -e

IMAGE_NAME="ghcr.io/antongrizli/telegram-heathy-bot"
TAG="latest"

echo "Building Docker image locally..."
docker build -t "${IMAGE_NAME}:${TAG}" .

echo "Running test suite inside the built image..."
docker run --rm -v "$(pwd)":/app -w /app -e PYTHONPATH=. "${IMAGE_NAME}:${TAG}" pytest -v

echo "Starting database and bot container using docker-compose..."
docker-compose up --build -d

echo "Bot container built, tested, and running locally!"
