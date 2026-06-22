#!/bin/bash

set -e
echo "#### LOADING BACKEND..."

echo "#### Running database migrations..."
alembic upgrade head
echo "#### END OF RUNNING MIGRATIONS..."

# Start the application
echo "Starting application..."
exec gunicorn app.main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
