from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import aiohttp
import os
import asyncio
from typing import Dict, List, Optional
import re
from contextlib import asynccontextmanager

# Global state
current_track = None
player_status = "stopped"
current_audio_url = None
http_session: Optional[aiohttp.ClientSession] = None

# YouTube Data API configuration
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage aiohttp session lifecycle"""
    global http_session
    http_session = aiohttp.ClientSession()
    yield
    await http_session.close()

app = FastAPI(
    title="Virus Music Radio API",
    version="4.3.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class YouTubeAPIService:
    def __init__(self):
        self.api_key = YOUTUBE_API_KEY
        self.base_url = YOUTUBE_API_URL
        self.has_ytdlp = self._check_ytdlp()

    def _check_ytdlp(self) -> bool:
        """Check if yt-dlp is available"""
        try:
            import yt_dlp
            return True
        except ImportError:
            print("⚠️ yt-dlp not installed, using fallback methods")
            return False

    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for music videos using YouTube Data API with aiohttp."""
        try:
            print(f"🔍 Searching via YouTube API: {query}")
            params = {
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'videoCategoryId': '10',
                'maxResults': limit,
                'key': self.api_key
            }

            async with http_session.get(
                f"{self.base_url}/search",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    print(f"❌ YouTube API Error: {response.status} - {text}")
                    return []

                data = await response.json()
                results = []

                for item in data.get('items', []):
                    video_id = item['id']['videoId']
                    snippet = item['snippet']
                    duration = await self.get_video_duration(video_id)

                    results.append({
                        'id': video_id,
                        'title': snippet['title'],
                        'url': f"https://www.youtube.com/watch?v={video_id}",
                        'duration': duration,
                        'thumbnail': snippet['thumbnails']['high']['url'],
                        'artist': snippet['channelTitle'],
                        'source': 'youtube_api'
                    })

                print(f"✅ Found {len(results)} results via YouTube API")
                return results

        except asyncio.TimeoutError:
            print("❌ YouTube API timeout")
            return []
        except Exception as e:
            print(f"❌ YouTube API search error: {e}")
            return []

    async def get_video_duration(self, video_id: str) -> int:
        """Get YouTube video duration in seconds using aiohttp."""
        try:
            params = {
                'part': 'contentDetails',
                'id': video_id,
                'key': self.api_key
            }

            async with http_session.get(
                f"{self.base_url}/videos",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('items'):
                        duration_str = data['items'][0]['contentDetails']['duration']
                        return self.parse_duration(duration_str)

            return 0
        except Exception as e:
            print(f"❌ Duration fetch error: {e}")
            return 0

    def parse_duration(self, duration: str) -> int:
        """Convert ISO 8601 duration (e.g. PT4M13S) to seconds."""
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    async def get_video_info(self, video_id: str) -> Optional[Dict]:
        """Fetch detailed video info using aiohttp."""
        try:
            params = {
                'part': 'snippet,contentDetails',
                'id': video_id,
                'key': self.api_key
            }

            async with http_session.get(
                f"{self.base_url}/videos",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('items'):
                        item = data['items'][0]
                        snippet = item['snippet']
                        return {
                            'id': video_id,
                            'title': snippet['title'],
                            'duration': self.parse_duration(item['contentDetails']['duration']),
                            'thumbnail': snippet['thumbnails']['high']['url'],
                            'artist': snippet['channelTitle'],
                            'description': snippet.get('description', '')[:100] + '...'
                        }

            return None
        except Exception as e:
            print(f"❌ Video info API error: {e}")
            return None

    async def get_audio_stream_url(self, youtube_url: str) -> Optional[str]:
        """Get audio stream URL with multiple fallback methods."""
        try:
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                return None

            print(f"🎵 Getting audio stream for: {video_id}")

            # Try yt-dlp if available
            if self.has_ytdlp:
                try:
                    import yt_dlp
                    ydl_opts = {
                        'format': 'bestaudio/best',
                        'quiet': True,
                        'noplaylist': True,
                        'extract_flat': False,
                    }

                    def extract_info():
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            return ydl.extract_info(youtube_url, download=False)

                    info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
                    
                    # Get best audio format
                    if 'url' in info:
                        print("✅ Stream via yt-dlp (direct)")
                        return info['url']
                    elif 'formats' in info:
                        audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                        if audio_formats:
                            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
                            print("✅ Stream via yt-dlp (format selection)")
                            return best_audio['url']
                            
                except Exception as e:
                    print(f"❌ yt-dlp failed: {e}")

            # Try proxy service fallback
            proxy_url = await self.get_proxy_stream(video_id)
            if proxy_url:
                return proxy_url

            # Last resort: Invidious public instances
            invidious_url = await self.get_invidious_stream(video_id)
            if invidious_url:
                return invidious_url

            # Absolute fallback
            print("⚠️ All methods failed, using fallback audio")
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"

        except Exception as e:
            print(f"❌ Audio stream error: {e}")
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"

    async def get_invidious_stream(self, video_id: str) -> Optional[str]:
        """Try Invidious public instances for audio stream."""
        invidious_instances = [
            "https://invidious.privacydev.net",
            "https://inv.tux.pizza",
        ]

        for instance in invidious_instances:
            try:
                async with http_session.get(
                    f"{instance}/api/v1/videos/{video_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        audio_formats = [f for f in data.get('adaptiveFormats', []) 
                                       if 'audio' in f.get('type', '')]
                        if audio_formats:
                            best_audio = max(audio_formats, key=lambda x: x.get('bitrate', 0))
                            print(f"✅ Stream via Invidious: {instance}")
                            return best_audio['url']
            except Exception as e:
                print(f"❌ Invidious {instance} failed: {e}")
                continue

        return None

    async def get_proxy_stream(self, video_id: str) -> Optional[str]:
        """Fallback proxy audio stream services."""
        # Note: These are placeholder services - replace with actual working proxies
        services = [
            f"https://api.cobalt.tools/api/json",  # Example: Cobalt API
        ]

        for service_url in services:
            try:
                payload = {"url": f"https://www.youtube.com/watch?v={video_id}"}
                async with http_session.post(
                    service_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'url' in data:
                            print(f"✅ Stream via proxy: {service_url}")
                            return data['url']
            except Exception as e:
                print(f"❌ Proxy {service_url} failed: {e}")
                continue

        return None

    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from various URL formats."""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&?/]+)',
            r'youtube\.com/embed/([^?]+)',
            r'^([a-zA-Z0-9_-]{11})$',  # Direct video ID
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

# Initialize YouTube service
youtube_service = YouTubeAPIService()

# API Endpoints
@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API",
        "status": "online",
        "version": "4.3.0",
        "features": {
            "yt_dlp": youtube_service.has_ytdlp,
            "youtube_api": bool(YOUTUBE_API_KEY)
        },
        "endpoints": {
            "search": "/api/search?q=query",
            "play": "POST /api/play",
            "stream": "/api/stream",
            "status": "/api/status",
            "stop": "POST /api/stop",
            "radio_url": "/api/radio/url"
        }
    }

@app.get("/api/search")
async def search_music(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=20)):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube API key not configured")
    results = await youtube_service.search_music(q, limit)
    return {"query": q, "results": results, "count": len(results)}

@app.post("/api/play")
async def play_music(video_url: str = Form(...)):
    global current_track, player_status, current_audio_url
    try:
        print(f"🎵 Play request: {video_url}")
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")

        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found")

        audio_url = await youtube_service.get_audio_stream_url(f"https://www.youtube.com/watch?v={video_id}")
        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")

        current_track = {**video_info, "url": audio_url, "source": "youtube_api"}
        current_audio_url = audio_url
        player_status = "playing"

        base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
        
        return {
            "status": "playing",
            "track": current_track,
            "radio_url": f"{base_url}/api/stream",
            "stream_url": f"{base_url}/api/stream",
            "message": f"🎵 Now playing: {current_track['title']} by {current_track['artist']}"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream")
async def stream_audio():
    global current_audio_url, player_status

    if player_status != "playing" or not current_audio_url:
        silent_audio = b'\x00' * 1024
        return StreamingResponse(iter([silent_audio]), media_type="audio/mpeg")

    try:
        async def generate():
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*',
                    'Range': 'bytes=0-',
                }
                
                async with http_session.get(
                    current_audio_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.content.iter_chunked(8192):
                        if chunk:
                            yield chunk
                            
            except Exception as e:
                print(f"❌ Stream error: {e}")
                # Send silence on error
                for _ in range(10):
                    yield b'\x00' * 8192

        return StreamingResponse(
            generate(),
            media_type="audio/mpeg",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
        )

    except Exception as e:
        print(f"❌ Stream proxy error: {e}")
        return StreamingResponse(iter([b'\x00' * 1024]), media_type="audio/mpeg")

@app.post("/api/stop")
async def stop_music():
    global current_track, player_status, current_audio_url
    current_track = None
    player_status = "stopped"
    current_audio_url = None
    print("🛑 Music stopped")
    return {"status": "stopped", "message": "Playback stopped"}

@app.get("/api/status")
async def get_player_status():
    return {
        "status": player_status,
        "current_track": current_track,
        "stream_active": player_status == "playing",
        "has_track": current_track is not None
    }

@app.get("/api/radio/url")
async def get_radio_url():
    base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
    return {
        "radio_url": f"{base_url}/api/stream",
        "status": player_status,
        "current_track": current_track['title'] if current_track else 'No track playing',
        "artist": current_track['artist'] if current_track else 'None',
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "4.3.0",
        "player_status": player_status,
        "features": {
            "yt_dlp": youtube_service.has_ytdlp,
            "youtube_api": bool(YOUTUBE_API_KEY)
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
