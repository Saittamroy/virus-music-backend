# 🎵 Virus Music Backend

Radio streaming backend for Highrise music bot. Streams YouTube music to Icecast radio server.

## Features
- 🎧 YouTube music streaming
- 📻 Icecast radio server
- 🔍 Music search API
- 🚀 FastAPI backend

## Deployment

Contact : Owner

### Environment Variables
None required - uses default configuration

## API Endpoints
- `GET /` - Health check
- `GET /api/search?q=query` - Search YouTube
- `POST /api/play` - Start radio stream
- `GET /api/radio/url` - Get stream URL
- `GET /api/status` - Player status

## Get Your Radio URL
After deployment, visit:
`https://your-app.railway.app/api/radio/url`