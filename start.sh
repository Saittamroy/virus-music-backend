#!/bin/bash

# Start Icecast in background
echo "🎧 Starting Icecast server..."
icecast2 -c /etc/icecast2/icecast.xml &

# Wait for Icecast to start
sleep 3

# Start FastAPI server
echo "🚀 Starting FastAPI server on port $PORT..."
uvicorn app:app --host 0.0.0.0 --port $PORT