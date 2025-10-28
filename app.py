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
        self.buffer_lock = asyncio.Lock()
        self.chunk_event = asyncio.Event()  # Signal when new audio is available
        self.stream_process = None  # FFmpeg process
        self.is_streaming = False
        self.playlist = deque()  # Queue for continuous playback
        self.stream_task = None  # Background streaming task

radio_state = RadioState()

# YouTube Data API configuration
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage aiohttp session lifecycle"""
    global http_session
    http_session = aiohttp.ClientSession()
    
    # Add some default songs to playlist on startup
    await add_default_songs()
    
    yield
    
    # Cleanup
    await http_session.close()
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
    if radio_state.stream_task:
        radio_state.stream_task.cancel()

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
        """Get audio stream URL with working fallback methods."""
        try:
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                return None

            logger.info(f"🎵 Getting audio stream for: {video_id}")

            # Method 1: Try yt-dlp with mobile user agent to avoid bot detection
            if self.has_ytdlp:
                try:
                    import yt_dlp
                    ydl_opts = {
                        'format': 'bestaudio/best',
                        'quiet': True,
                        'noplaylist': True,
                        'no_warnings': True,
                        'extract_flat': False,
                        'socket_timeout': 30,
                        # Use mobile user agent to avoid bot detection
                        'user_agent': 'Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36',
                        'http_headers': {
                            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Accept-Language': 'en-us,en;q=0.5',
                            'Accept-Encoding': 'gzip, deflate',
                            'Connection': 'keep-alive',
                        },
                        'extractor_args': {
                            'youtube': {
                                'player_client': ['android', 'web'],
                                'player_skip': ['configs', 'webpage'],
                            }
                        },
                    }

                    def extract_info():
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(youtube_url, download=False)
                            return info

                    info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
                    
                    if info and 'url' in info:
                        logger.info("✅ Stream via yt-dlp (direct URL)")
                        return info['url']
                    elif info and 'formats' in info:
                        # Try to find a working audio format
                        formats = info['formats']
                        
                        # Prefer m4a audio formats (usually more reliable)
                        m4a_formats = [f for f in formats if f.get('ext') == 'm4a' and f.get('acodec') != 'none']
                        if m4a_formats:
                            best_m4a = max(m4a_formats, key=lambda x: x.get('abr', 0) or 0)
                            logger.info("✅ Stream via yt-dlp (m4a format)")
                            return best_m4a['url']
                        
                        # Fallback to any audio format
                        audio_formats = [f for f in formats if f.get('acodec') != 'none']
                        if audio_formats:
                            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
                            logger.info(f"✅ Stream via yt-dlp ({best_audio.get('ext', 'audio')} format)")
                            return best_audio['url']
                        
                except Exception as e:
                    logger.error(f"❌ yt-dlp failed: {str(e)[:100]}")

            # Method 2: Try Piped API (alternative to Invidious)
            piped_url = await self.get_piped_stream(video_id)
            if piped_url:
                return piped_url

            # Method 3: Try different Invidious instances
            invidious_url = await self.get_invidious_stream_fallback(video_id)
            if invidious_url:
                return invidious_url

            logger.error("❌ All audio stream methods failed")
            return None

        except Exception as e:
            logger.error(f"❌ Audio stream error: {e}")
            return None

    async def get_piped_stream(self, video_id: str) -> Optional[str]:
        """Try Piped API instances."""
        piped_instances = [
            "https://pipedapi.kavin.rocks",
            "https://pipedapi.in.projectsegfau.lt", 
            "https://api.piped.privacydev.net",
        ]

        for instance in piped_instances:
            try:
                logger.info(f"🔍 Trying Piped: {instance}")
                async with http_session.get(
                    f"{instance}/streams/{video_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        audio_streams = data.get('audioStreams', [])
                        if audio_streams:
                            # Get the best quality audio
                            best_audio = max(audio_streams, key=lambda x: x.get('bitrate', 0))
                            logger.info(f"✅ Stream via Piped: {instance}")
                            return best_audio['url']
            except Exception as e:
                logger.warning(f"❌ Piped {instance} failed: {e}")
                continue

        return None

    async def get_invidious_stream_fallback(self, video_id: str) -> Optional[str]:
        """Try multiple Invidious instances with better error handling."""
        invidious_instances = [
            "https://yt.artemislena.eu",
            "https://invidious.flokinet.to", 
            "https://inv.nadeko.net",
            "https://yewtu.be",
        ]

        for instance in invidious_instances:
            try:
                logger.info(f"🔍 Trying Invidious: {instance}")
                async with http_session.get(
                    f"{instance}/api/v1/videos/{video_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        adaptive_formats = data.get('adaptiveFormats', [])
                        audio_formats = [f for f in adaptive_formats if 'audio' in f.get('type', '')]
                        if audio_formats:
                            best_audio = max(audio_formats, key=lambda x: x.get('bitrate', 0))
                            logger.info(f"✅ Stream via Invidious: {instance}")
                            return best_audio['url']
            except Exception as e:
                logger.warning(f"❌ Invidious {instance} failed: {e}")
                continue

        return None

# Initialize YouTube service
youtube_service = YouTubeAPIService()

async def add_default_songs():
    """Add default songs to playlist on startup."""
    default_songs = [
        "https://www.youtube.com/watch?v=kJQP7kiw5Fk",  # Despacito
        "https://www.youtube.com/watch?v=fJ9rUzIMcZQ",  # Bohemian Rhapsody
        "https://www.youtube.com/watch?v=JGwWNGJdvx8",  # Shape of You
    ]
    
    for url in default_songs:
        video_id = youtube_service.extract_video_id(url)
        if video_id:
            info = await youtube_service.get_video_info(video_id)
            if info:
                radio_state.playlist.append({**info, 'url': url})
                logger.info(f"✅ Added to playlist: {info['title']}")
    
    if radio_state.playlist:
        logger.info(f"📋 Playlist initialized with {len(radio_state.playlist)} songs")
        # Start continuous streaming
        asyncio.create_task(continuous_stream_loop())

async def continuous_stream_loop():
    """Continuous streaming loop that plays music 24/7 regardless of listeners."""
    logger.info("🎵 Starting continuous radio stream (24/7 mode)")
    
    while True:
        try:
            # Wait for playlist to have songs
            while not radio_state.playlist:
                logger.warning("⚠️ Playlist empty, waiting for songs...")
                await asyncio.sleep(5)
            
            # Get next track from playlist
            track = radio_state.playlist.popleft()
            radio_state.current_track = track
            radio_state.player_status = "playing"
            
            logger.info(f"▶️ NOW PLAYING: {track['title']} (Listeners: {len(radio_state.listeners)})")
            
            # Get audio stream URL
            audio_url = await youtube_service.get_audio_stream_url(track['url'])
            
            if audio_url:
                # Stream this track
                await stream_audio_to_buffer(audio_url)
            else:
                logger.warning(f"⚠️ Could not get audio for: {track['title']}")
                # Put track back at end of playlist
                radio_state.playlist.append(track)
                await asyncio.sleep(2)
            
            # Track finished, continue to next
            logger.info(f"✅ Track completed: {track['title']}")
            
        except Exception as e:
            logger.error(f"❌ Radio loop error: {e}")
            await asyncio.sleep(1)

async def stream_audio_to_buffer(audio_url: str):
    """Stream audio continuously to buffer using FFmpeg."""
    process = None
    max_retries = 2
    retry_count = 0
    
    while retry_count < max_retries and radio_state.player_status == "playing":
        try:
            logger.info(f"🎵 Starting audio stream (attempt {retry_count + 1}/{max_retries})")
            
            # FFmpeg command with streaming optimizations
            cmd = [
                'ffmpeg',
                '-i', audio_url,
                '-reconnect', '1',
                '-reconnect_streamed', '1',
                '-reconnect_delay_max', '5',
                '-fflags', '+genpts+discardcorrupt',
                '-vn',
                '-acodec', 'libmp3lame',
                '-b:a', '128k',
                '-ar', '44100', 
                '-ac', '2',
                '-f', 'mp3',
                '-fflags', '+nobuffer',
                '-flags', 'low_delay',
                '-max_delay', '50000',
                'pipe:1'
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )

            radio_state.stream_process = process
            radio_state.is_streaming = True
            
            logger.info("✅ FFmpeg process started")
            
            # Read and buffer audio
            chunk_count = 0
            start_time = asyncio.get_event_loop().time()
            
            while radio_state.player_status == "playing" and process.poll() is None:
                chunk = process.stdout.read(4096)
                if not chunk:
                    if process.poll() is not None:
                        break
                    await asyncio.sleep(0.01)
                    continue
                
                chunk_count += 1
                
                # Add to buffer
                async with radio_state.buffer_lock:
                    radio_state.audio_buffer.append(chunk)
                    if chunk_count % 3 == 0:  # Frequent updates for live streaming
                        radio_state.chunk_event.set()
                
                # Log progress every 10 seconds
                current_time = asyncio.get_event_loop().time()
                if current_time - start_time >= 10.0:
                    logger.info(f"📦 Streaming: {chunk_count} chunks (Listeners: {len(radio_state.listeners)})")
                    start_time = current_time
                
                await asyncio.sleep(0.001)
            
            # Check exit status
            return_code = process.poll()
            if return_code == 0:
                logger.info("✅ Stream completed successfully")
                break
            else:
                logger.warning(f"⚠️ Stream interrupted, retrying... (code: {return_code})")
                retry_count += 1
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.error(f"❌ Stream error: {e}")
            retry_count += 1
            await asyncio.sleep(2)
        finally:
            if process and process.poll() is None:
                process.terminate()
    
    if retry_count >= max_retries:
        logger.error("❌ Max retries reached, moving to next track")
    
    radio_state.is_streaming = False

# API Endpoints
@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API (24/7 Live Broadcasting)",
        "status": "online",
        "version": "4.3.0",
        "listeners": len(radio_state.listeners),
        "streaming": radio_state.is_streaming,
        "current_track": radio_state.current_track['title'] if radio_state.current_track else None,
        "playlist_size": len(radio_state.playlist),
        "features": {
            "yt_dlp": youtube_service.has_ytdlp,
            "youtube_api": bool(YOUTUBE_API_KEY),
            "live_broadcast": True,
            "continuous_playback": True
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
    """Add song to playlist and ensure streaming is active."""
    try:
        logger.info(f"🎵 Adding to playlist: {video_url}")
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")

        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found")

        # Add to playlist
        track = {**video_info, "url": video_url}
        radio_state.playlist.append(track)

        # Ensure streaming is active
        if radio_state.player_status != "playing" and not radio_state.is_streaming:
            if not radio_state.stream_task or radio_state.stream_task.done():
                radio_state.stream_task = asyncio.create_task(continuous_stream_loop())

        base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
        
        return {
            "status": "added_to_playlist",
            "track": track,
            "position": len(radio_state.playlist),
            "radio_url": f"{base_url}/api/stream", 
            "listeners": len(radio_state.listeners),
            "message": f"🎵 Added to playlist: {track['title']} by {track['artist']}"
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
            "icy-name": "Virus Radio 24/7",
            "icy-genre": "Various",
        }
    )

@app.post("/api/stop")
async def stop_music():
    """Stop the current track but keep the streaming loop running."""
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
        radio_state.is_streaming = False
    
    radio_state.current_track = None
    radio_state.player_status = "stopped"
    radio_state.current_audio_url = None
    
    # Clear buffer
    async with radio_state.buffer_lock:
        radio_state.audio_buffer.clear()
    
    logger.info("🛑 Current track stopped")
    return {
        "status": "stopped", 
        "message": "Current playback stopped",
        "listeners": len(radio_state.listeners),
        "streaming_loop": "active" if radio_state.stream_task and not radio_state.stream_task.done() else "inactive"
    }

@app.get("/api/status")
async def get_player_status():
    return {
        "status": radio_state.player_status,
        "current_track": radio_state.current_track,
        "stream_active": radio_state.player_status == "playing",
        "listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_buffer),
        "is_streaming": radio_state.is_streaming,
        "playlist_size": len(radio_state.playlist),
        "continuous_mode": True
    }

@app.get("/api/playlist")
async def get_playlist():
    """Get current playlist."""
    return {
        "current": radio_state.current_track,
        "queue": list(radio_state.playlist),
        "total": len(radio_state.playlist)
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
        "live_broadcast": True,
        "continuous_playback": True
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "4.3.0",
        "player_status": radio_state.player_status,
        "listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_buffer),
        "playlist_size": len(radio_state.playlist),
        "features": {
            "yt_dlp": youtube_service.has_ytdlp,
            "youtube_api": bool(YOUTUBE_API_KEY),
            "live_broadcast": True,
            "continuous_playback": True
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
