#!/usr/bin/env bash
set -e

echo "Starting check.sh"

echo "Checking style..."
uv run ruff check .

echo "Running tests..."
uv run pytest