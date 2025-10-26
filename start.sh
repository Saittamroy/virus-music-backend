#!/bin/bash

# Fix permissions for Icecast
chown -R icecast:icecast /var/log/icecast2

# Start Icecast in background
echo "🎧 Starting Icecast server..."
sudo -u icecast icecast2 -c /etc/icecast2/icecast.xml &

# Wait for Icecast to start
sleep 5

# Start FastAPI server
echo "🚀 Starting FastAPI server on port $PORT..."
uvicorn app:app --host 0.0.0.0 --port $PORT