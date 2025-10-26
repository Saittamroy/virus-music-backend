FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    icecast2 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create icecast user
RUN useradd -m icecast

# Copy Icecast config
COPY icecast.xml /etc/icecast2/icecast.xml
RUN chown icecast:icecast /etc/icecast2/icecast.xml

# Create app directory
WORKDIR /app

# Copy Python requirements
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application
COPY . .

# Create startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 8000 8001

CMD ["./start.sh"]