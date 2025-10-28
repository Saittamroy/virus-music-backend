from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import aiohttp
import os
import asyncio
from typing import Dict, List, Optional, AsyncIterator
import re
import subprocess
from collections import deque
from datetime import datetime
import logging
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state for live broadcasting
class RadioState:
    def __init__(self):
        self.current_track = None
        self.player_status = "stopped"
        self.current_audio_url = None
        self.audio_buffer = deque(maxlen=1000)  # Circular buffer for audio chunks
        self.listeners = set()  # Track active listeners
        self.buffer_position = 0  # Current position in buffer
        self.buffer_lock = asyncio.Lock()
        self.chunk_event = asyncio.Event()  # Signal when new audio is available
        self.stream_process = None  # FFmpeg process
        self.is_streaming = False

radio_state = RadioState()

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
    # Cleanup streaming process
    if radio_state.stream_process:
        radio_state.stream_process.terminate()

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
            logger.warning("⚠️ yt-dlp not installed, using fallback methods")
            return False

    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for music videos using YouTube Data API with aiohttp."""
        try:
            logger.info(f"🔍 Searching via YouTube API: {query}")
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
                    logger.error(f"❌ YouTube API Error: {response.status} - {text}")
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

                logger.info(f"✅ Found {len(results)} results via YouTube API")
                return results

        except asyncio.TimeoutError:
            logger.error("❌ YouTube API timeout")
            return []
        except Exception as e:
            logger.error(f"❌ YouTube API search error: {e}")
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
            logger.error(f"❌ Duration fetch error: {e}")
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
            logger.error(f"❌ Video info API error: {e}")
            return None

    async def get_audio_stream_url(self, youtube_url: str) -> Optional[str]:
        """Get audio stream URL with multiple fallback methods."""
        try:
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                return None

            logger.info(f"🎵 Getting audio stream for: {video_id}")

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
                        logger.info("✅ Stream via yt-dlp (direct)")
                        return info['url']
                    elif 'formats' in info:
                        audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                        if audio_formats:
                            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
                            logger.info("✅ Stream via yt-dlp (format selection)")
                            return best_audio['url']
                            
                except Exception as e:
                    logger.error(f"❌ yt-dlp failed: {e}")

            # Try Invidious public instances
            invidious_url = await self.get_invidious_stream(video_id)
            if invidious_url:
                return invidious_url

            logger.error("❌ All audio stream methods failed")
            return None

        except Exception as e:
            logger.error(f"❌ Audio stream error: {e}")
            return None

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
                            logger.info(f"✅ Stream via Invidious: {instance}")
                            return best_audio['url']
            except Exception as e:
                logger.error(f"❌ Invidious {instance} failed: {e}")
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

async def stream_audio_to_buffer(audio_url: str):
    """Stream audio continuously to buffer using FFmpeg."""
    try:
        logger.info("🎵 Starting continuous audio stream to buffer")
        
        # FFmpeg command to stream audio to MP3
        cmd = [
            'ffmpeg',
            '-i', audio_url,
            '-vn',
            '-acodec', 'libmp3lame',
            '-b:a', '128k',
            '-ar', '44100',
            '-ac', '2',
            '-f', 'mp3',
            'pipe:1'
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=8192
        )

        radio_state.stream_process = process
        radio_state.is_streaming = True
        
        # Continuously read audio and add to buffer
        while radio_state.is_streaming and process.poll() is None:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            
            # Add to circular buffer
            async with radio_state.buffer_lock:
                radio_state.audio_buffer.append(chunk)
                radio_state.chunk_event.set()  # Notify listeners
            
            await asyncio.sleep(0.01)  # Small delay to prevent CPU overload
        
        if process.poll() is None:
            process.terminate()
            
        logger.info("🛑 Audio streaming stopped")
        
    except Exception as e:
        logger.error(f"❌ Stream to buffer error: {e}")
    finally:
        radio_state.is_streaming = False
        if process and process.poll() is None:
            process.terminate()

# API Endpoints
@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API (Live Broadcasting)",
        "status": "online",
        "version": "4.3.0",
        "listeners": len(radio_state.listeners),
        "streaming": radio_state.is_streaming,
        "features": {
            "yt_dlp": youtube_service.has_ytdlp,
            "youtube_api": bool(YOUTUBE_API_KEY),
            "live_broadcast": True
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
    global http_session
    try:
        logger.info(f"🎵 Play request: {video_url}")
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")

        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found")

        audio_url = await youtube_service.get_audio_stream_url(f"https://www.youtube.com/watch?v={video_id}")
        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")

        # Stop previous stream if any
        if radio_state.stream_process:
            radio_state.stream_process.terminate()
            radio_state.is_streaming = False
        
        # Clear buffer for new track
        async with radio_state.buffer_lock:
            radio_state.audio_buffer.clear()
            radio_state.buffer_position = 0
        
        # Update state
        radio_state.current_track = {**video_info, "url": audio_url, "source": "youtube_api"}
        radio_state.current_audio_url = audio_url
        radio_state.player_status = "playing"

        # Start streaming to buffer in background
        asyncio.create_task(stream_audio_to_buffer(audio_url))

        base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
        
        return {
            "status": "playing",
            "track": radio_state.current_track,
            "radio_url": f"{base_url}/api/stream",
            "listeners": len(radio_state.listeners),
            "message": f"🎵 Now playing: {radio_state.current_track['title']} by {radio_state.current_track['artist']}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream")
async def stream_audio():
    """Live broadcast stream - all users hear the same audio at the same time."""
    
    listener_id = id(asyncio.current_task())
    radio_state.listeners.add(listener_id)
    
    logger.info(f"👤 New listener connected (Total: {len(radio_state.listeners)})")
    
    async def generate_live_audio() -> AsyncIterator[bytes]:
        try:
            # Start from current buffer position (live join)
            current_position = max(0, len(radio_state.audio_buffer) - 10)
            
            while radio_state.player_status == "playing":
                # Get current buffer size
                async with radio_state.buffer_lock:
                    buffer_size = len(radio_state.audio_buffer)
                
                # If we have buffered audio ahead, send it
                if current_position < buffer_size:
                    async with radio_state.buffer_lock:
                        chunk = radio_state.audio_buffer[current_position]
                    yield chunk
                    current_position += 1
                else:
                    # Wait for new audio chunks
                    try:
                        await asyncio.wait_for(radio_state.chunk_event.wait(), timeout=1.0)
                        radio_state.chunk_event.clear()
                    except asyncio.TimeoutError:
                        # Send silence to keep connection alive
                        yield b'\x00' * 4096
                
        except asyncio.CancelledError:
            logger.info(f"👤 Listener disconnected (Total: {len(radio_state.listeners) - 1})")
        except Exception as e:
            logger.error(f"❌ Stream generation error: {e}")
        finally:
            radio_state.listeners.discard(listener_id)
    
    return StreamingResponse(
        generate_live_audio(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "icy-br": "128",
            "icy-name": "Virus Radio Live",
            "icy-genre": "Various",
        }
    )

@app.post("/api/stop")
async def stop_music():
    """Stop the live broadcast."""
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
        radio_state.is_streaming = False
    
    radio_state.current_track = None
    radio_state.player_status = "stopped"
    radio_state.current_audio_url = None
    
    # Clear buffer
    async with radio_state.buffer_lock:
        radio_state.audio_buffer.clear()
        radio_state.buffer_position = 0
    
    logger.info("🛑 Music stopped")
    return {
        "status": "stopped", 
        "message": "Playback stopped",
        "listeners": len(radio_state.listeners)
    }

@app.get("/api/status")
async def get_player_status():
    return {
        "status": radio_state.player_status,
        "current_track": radio_state.current_track,
        "stream_active": radio_state.player_status == "playing",
        "listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_buffer),
        "is_streaming": radio_state.is_streaming
    }

@app.get("/api/radio/url")
async def get_radio_url():
    base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
    return {
        "radio_url": f"{base_url}/api/stream",
        "status": radio_state.player_status,
        "current_track": radio_state.current_track['title'] if radio_state.current_track else 'No track playing',
        "artist": radio_state.current_track['artist'] if radio_state.current_track else 'None',
        "listeners": len(radio_state.listeners),
        "live_broadcast": True
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "4.3.0",
        "player_status": radio_state.player_status,
        "listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_buffer),
        "features": {
            "yt_dlp": youtube_service.has_ytdlp,
            "youtube_api": bool(YOUTUBE_API_KEY),
            "live_broadcast": True
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
