#!/usr/bin/env bash
set -e

IMAGE_NAME="telegram-healthy-bot"
TAG="latest"

echo "Building Docker image locally..."
docker build -t "${IMAGE_NAME}:${TAG}" .

echo "Starting database and bot container using docker-compose..."
docker-compose up --build -d

echo "Bot container built and running locally!"
