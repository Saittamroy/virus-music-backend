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
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Continuous Radio Stream API", version="5.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== GLOBAL STATE ====================
class RadioState:
    def __init__(self):
        self.playlist: deque = deque(maxlen=100)
        self.current_track: Optional[Dict] = None
        self.is_streaming = False
        self.stream_process: Optional[subprocess.Popen] = None
        self.audio_chunks: deque = deque(maxlen=500)  # Circular buffer - keeps last ~500 chunks
        self.listeners = set()
        self.stream_started_at: Optional[datetime] = None
        self.buffer_lock = asyncio.Lock()
        self.chunk_event = asyncio.Event()  # Signal when new chunk available
        
radio_state = RadioState()

# YouTube API
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

# ==================== YOUTUBE SERVICE ====================
class YouTubeAPIService:
    def __init__(self):
        self.api_key = YOUTUBE_API_KEY
        self.base_url = YOUTUBE_API_URL
        self.session: Optional[aiohttp.ClientSession] = None

    async def init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        await self.init_session()
        try:
            params = {
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'videoCategoryId': '10',
                'maxResults': limit,
                'key': self.api_key
            }

            async with self.session.get(f"{self.base_url}/search", params=params) as response:
                if response.status != 200:
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

                return results

        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

    async def get_video_duration(self, video_id: str) -> int:
        try:
            params = {
                'part': 'contentDetails',
                'id': video_id,
                'key': self.api_key
            }

            async with self.session.get(f"{self.base_url}/videos", params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('items'):
                        duration_str = data['items'][0]['contentDetails']['duration']
                        return self.parse_duration(duration_str)
            return 0
        except:
            return 0

    def parse_duration(self, duration: str) -> int:
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    async def get_video_info(self, video_id: str) -> Optional[Dict]:
        await self.init_session()
        try:
            params = {
                'part': 'snippet,contentDetails',
                'id': video_id,
                'key': self.api_key
            }

            async with self.session.get(f"{self.base_url}/videos", params=params) as response:
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
                        }
            return None
        except Exception as e:
            logger.error(f"Video info error: {e}")
            return None

    def extract_video_id(self, url: str) -> Optional[str]:
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&?/]+)',
            r'youtube\.com/embed/([^?]+)',
            r'^([a-zA-Z0-9_-]{11})$',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

youtube_service = YouTubeAPIService()

# ==================== CONTINUOUS STREAMING ENGINE ====================

async def get_audio_stream_invidious(youtube_url: str) -> Optional[str]:
    """Get audio stream from Invidious instances"""
    try:
        video_id = youtube_service.extract_video_id(youtube_url)
        if not video_id:
            return None
        
        # Try multiple Invidious instances
        invidious_instances = [
            "https://invidious.privacydev.net",
            "https://inv.tux.pizza",
            "https://invidious.lunar.icu",
            "https://yt.artemislena.eu",
        ]
        
        for instance in invidious_instances:
            try:
                logger.info(f"🔍 Trying Invidious: {instance}")
                
                async with youtube_service.session.get(
                    f"{instance}/api/v1/videos/{video_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Get audio formats
                        audio_formats = [
                            f for f in data.get('adaptiveFormats', []) 
                            if 'audio' in f.get('type', '').lower()
                        ]
                        
                        if audio_formats:
                            # Get best quality audio
                            best_audio = max(audio_formats, key=lambda x: x.get('bitrate', 0))
                            audio_url = best_audio.get('url')
                            
                            if audio_url:
                                logger.info(f"✅ Got stream from {instance}")
                                return audio_url
            except Exception as e:
                logger.warning(f"❌ Invidious {instance} failed: {e}")
                continue
        
        return None
    except Exception as e:
        logger.error(f"Invidious error: {e}")
        return None

async def get_audio_stream_piped(youtube_url: str) -> Optional[str]:
    """Get audio stream from Piped API"""
    try:
        video_id = youtube_service.extract_video_id(youtube_url)
        if not video_id:
            return None
        
        piped_instances = [
            "https://pipedapi.kavin.rocks",
            "https://api.piped.privacydev.net",
        ]
        
        for instance in piped_instances:
            try:
                logger.info(f"🔍 Trying Piped: {instance}")
                
                async with youtube_service.session.get(
                    f"{instance}/streams/{video_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Get audio URL
                        audio_streams = data.get('audioStreams', [])
                        if audio_streams:
                            best_audio = max(audio_streams, key=lambda x: x.get('bitrate', 0))
                            audio_url = best_audio.get('url')
                            
                            if audio_url:
                                logger.info(f"✅ Got stream from Piped")
                                return audio_url
            except Exception as e:
                logger.warning(f"❌ Piped {instance} failed: {e}")
                continue
        
        return None
    except Exception as e:
        logger.error(f"Piped error: {e}")
        return None

async def get_audio_stream_with_ytdlp(youtube_url: str) -> Optional[str]:
    """Get audio stream URL using yt-dlp with anti-bot measures"""
    try:
        import yt_dlp
        
        # Anti-bot configuration
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
            'extract_flat': False,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'skip': ['hls', 'dash']
                }
            },
            # Use mobile user agent to avoid bot detection
            'user_agent': 'Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36',
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.162 Mobile Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
        }

        def extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
                return info

        info = await asyncio.get_event_loop().run_in_executor(None, extract)
        
        if info and 'url' in info:
            logger.info(f"✅ yt-dlp got direct URL")
            return info['url']
        elif info and 'formats' in info:
            # Find the best audio format
            audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            if audio_formats:
                # Prefer formats with known audio bitrate
                best_audio = None
                for fmt in audio_formats:
                    if fmt.get('abr'):
                        if not best_audio or fmt.get('abr', 0) > best_audio.get('abr', 0):
                            best_audio = fmt
                
                if not best_audio and audio_formats:
                    best_audio = audio_formats[0]
                
                if best_audio and 'url' in best_audio:
                    logger.info(f"✅ yt-dlp got format URL with abr: {best_audio.get('abr')}")
                    return best_audio['url']
        
        logger.error("❌ yt-dlp could not extract audio URL")
        return None
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")
        return None

async def get_audio_stream_multi_source(youtube_url: str) -> Optional[str]:
    """Try multiple sources to get audio stream"""
    
    # Method 1: Try yt-dlp with anti-bot measures
    logger.info("🎵 Method 1: yt-dlp with anti-bot config")
    audio_url = await get_audio_stream_with_ytdlp(youtube_url)
    if audio_url:
        return audio_url
    
    # Method 2: Try Invidious
    logger.info("🎵 Method 2: Invidious instances")
    audio_url = await get_audio_stream_invidious(youtube_url)
    if audio_url:
        return audio_url
    
    # Method 3: Try Piped
    logger.info("🎵 Method 3: Piped API")
    audio_url = await get_audio_stream_piped(youtube_url)
    if audio_url:
        return audio_url
    
    logger.error(f"❌ All methods failed for: {youtube_url}")
    return None

async def stream_audio_to_buffer(audio_url: str):
    """
    Stream audio data into circular buffer continuously.
    This runs ALWAYS, regardless of listeners.
    """
    process = None
    try:
        logger.info(f"🎵 Starting FFmpeg stream for: {radio_state.current_track['title']}")
        logger.info(f"🔗 Audio URL: {audio_url[:100]}...")
        
        # FFmpeg command to convert any audio to MP3 stream with better error handling
        cmd = [
            'ffmpeg',
            '-i', audio_url,
            '-vn',  # No video
            '-acodec', 'libmp3lame',
            '-b:a', '128k',
            '-ar', '44100',
            '-ac', '2',
            '-f', 'mp3',
            '-',  # Output to stdout
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # Capture stderr for debugging
            bufsize=8192
        )

        radio_state.stream_process = process
        
        # Read and buffer audio chunks CONTINUOUSLY
        chunk_count = 0
        while radio_state.is_streaming and process.poll() is None:
            chunk = process.stdout.read(4096)  # Smaller chunks for better streaming
            if not chunk:
                # Check if process ended
                if process.poll() is not None:
                    break
                # Small delay before reading again
                await asyncio.sleep(0.1)
                continue
            
            chunk_count += 1
            
            # Store in circular buffer (always, even without listeners)
            async with radio_state.buffer_lock:
                radio_state.audio_chunks.append(chunk)
                if chunk_count % 10 == 0:  # Set event every 10 chunks to avoid too many events
                    radio_state.chunk_event.set()
            
            # Log progress occasionally
            if chunk_count % 100 == 0:
                logger.info(f"📦 Buffered {chunk_count} chunks for {radio_state.current_track['title']}")
            
            # Small delay to prevent CPU spinning
            await asyncio.sleep(0)
        
        # Check why we exited the loop
        if process.poll() is not None:
            stderr_output = process.stderr.read().decode('utf-8', errors='ignore')
            if stderr_output:
                logger.error(f"FFmpeg stderr: {stderr_output[:500]}")
            logger.info(f"✅ FFmpeg process ended with code: {process.returncode}")
        else:
            logger.info(f"✅ Finished streaming: {radio_state.current_track['title']}")
        
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        if process and process.poll() is None:
            process.terminate()
    finally:
        if process and process.poll() is None:
            process.terminate()

async def add_default_songs():
    """Add default songs to playlist on startup"""
    logger.info("📋 Adding default tracks to playlist...")
    
    # Try with YouTube API first
    if YOUTUBE_API_KEY:
        default_songs = [
            "kJQP7kiw5Fk",  # Despacito
            "fJ9rUzIMcZQ",  # Bohemian Rhapsody
            "RgKAFK5djSk",  # Wiz Khalifa - See You Again
            "CevxZvSJLk8",  # Katy Perry - Roar
            "OPf0YbXqDm0",  # Mark Ronson - Uptown Funk
        ]
        
        for video_id in default_songs:
            try:
                info = await youtube_service.get_video_info(video_id)
                if info:
                    url = f"https://www.youtube.com/watch?v={video_id}"
                    radio_state.playlist.append({**info, 'url': url})
                    logger.info(f"✅ Added: {info['title']}")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"❌ Failed to add {video_id}: {e}")
        
        if radio_state.playlist:
            logger.info(f"📋 Playlist ready with {len(radio_state.playlist)} songs")
            return
    
    # Fallback: Add songs manually without full metadata
    logger.warning("⚠️ YouTube API unavailable, using fallback songs")
    fallback_songs = [
        {
            'id': 'kJQP7kiw5Fk',
            'title': 'Luis Fonsi - Despacito',
            'url': 'https://www.youtube.com/watch?v=kJQP7kiw5Fk',
            'duration': 280,
            'thumbnail': '',
            'artist': 'Luis Fonsi',
        },
        {
            'id': 'fJ9rUzIMcZQ',
            'title': 'Queen - Bohemian Rhapsody',
            'url': 'https://www.youtube.com/watch?v=fJ9rUzIMcZQ',
            'duration': 354,
            'thumbnail': '',
            'artist': 'Queen',
        },
    ]
    
    for song in fallback_songs:
        radio_state.playlist.append(song)
        logger.info(f"✅ Added (fallback): {song['title']}")
    
    logger.info(f"📋 Fallback playlist ready with {len(radio_state.playlist)} songs")

async def continuous_radio_loop():
    """
    Main radio loop - plays songs continuously in background.
    This ALWAYS runs when is_streaming=True, regardless of listeners.
    """
    logger.info("🎵 Starting continuous radio loop (24/7 mode)")
    radio_state.is_streaming = True
    radio_state.stream_started_at = datetime.now()
    
    # Add default songs if playlist is empty
    if not radio_state.playlist:
        await add_default_songs()
        # Wait a bit for playlist to populate
        await asyncio.sleep(2)
    
    while radio_state.is_streaming:
        try:
            # Wait for playlist to have songs
            wait_count = 0
            while not radio_state.playlist and radio_state.is_streaming:
                if wait_count % 10 == 0:  # Log every 10 iterations
                    logger.warning(f"⚠️ Playlist empty, waiting for songs... (attempt {wait_count + 1})")
                await asyncio.sleep(1)
                wait_count += 1
                if wait_count > 30:  # 30 second timeout
                    logger.error("❌ Timeout waiting for playlist, adding defaults")
                    await add_default_songs()
                    break
            
            if not radio_state.is_streaming:
                break
            
            # Double-check playlist is not empty
            if not radio_state.playlist:
                logger.error("❌ Playlist still empty after waiting")
                await asyncio.sleep(5)
                continue
            
            # Get next track
            track = radio_state.playlist.popleft()
            radio_state.current_track = track
            
            logger.info(f"▶️ NOW PLAYING: {track['title']} (Duration: {track['duration']}s, Listeners: {len(radio_state.listeners)})")
            
            # Get audio stream URL using multiple fallback methods
            audio_url = await get_audio_stream_multi_source(track['url'])
            
            if audio_url:
                # Stream this track to buffer (runs continuously)
                await stream_audio_to_buffer(audio_url)
            else:
                logger.warning(f"⚠️ Could not get audio for: {track['title']}")
                # Put track back at the end of playlist to retry later
                radio_state.playlist.append(track)
                await asyncio.sleep(2)
            
            # Track finished, continue to next
            logger.info(f"✅ Track completed. Queue size: {len(radio_state.playlist)}")
            
        except Exception as e:
            logger.error(f"Radio loop error: {e}")
            await asyncio.sleep(1)
    
    logger.info("🛑 Radio loop stopped")

# Background streaming task
streaming_task: Optional[asyncio.Task] = None

async def start_continuous_stream():
    """Start the continuous radio stream (24/7)"""
    global streaming_task
    
    if streaming_task and not streaming_task.done():
        logger.info("✅ Stream already running")
        return
    
    # Clear old buffer
    radio_state.audio_chunks.clear()
    
    # Start streaming loop in background
    streaming_task = asyncio.create_task(continuous_radio_loop())
    logger.info("✅ Continuous stream started (24/7 mode)")

async def stop_continuous_stream():
    """Stop the continuous radio stream"""
    global streaming_task
    
    logger.info("🛑 Stopping continuous stream...")
    radio_state.is_streaming = False
    
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
    
    if streaming_task:
        streaming_task.cancel()
        try:
            await streaming_task
        except asyncio.CancelledError:
            pass
    
    radio_state.current_track = None
    radio_state.audio_chunks.clear()
    logger.info("🛑 Continuous stream stopped")

# ==================== API ENDPOINTS ====================

@app.on_event("startup")
async def startup_event():
    """Initialize and auto-start stream on startup"""
    logger.info("🚀 API Starting...")
    await youtube_service.init_session()
    
    # Give session time to initialize
    await asyncio.sleep(2)
    
    # Add default songs before starting stream
    if not radio_state.playlist:
        await add_default_songs()
    
    # Wait a bit for playlist to populate
    await asyncio.sleep(1)
    
    # Auto-start streaming
    if radio_state.playlist:
        await start_continuous_stream()
        logger.info("🚀 API Started - Radio streaming 24/7")
    else:
        logger.warning("⚠️ Could not add default songs. Use !play to add music.")
        logger.info("🚀 API Started - Waiting for songs")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    await stop_continuous_stream()
    await youtube_service.close_session()
    logger.info("👋 API Shutdown")

@app.get("/")
async def root():
    return {
        "message": "Continuous Radio Stream API (24/7)",
        "status": "online",
        "version": "5.1.0",
        "streaming": radio_state.is_streaming,
        "current_track": radio_state.current_track['title'] if radio_state.current_track else None,
        "playlist_size": len(radio_state.playlist),
        "active_listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_chunks)
    }

@app.get("/api/search")
async def search_music(q: str = Query(...), limit: int = Query(10, ge=1, le=20)):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube API not configured")
    results = await youtube_service.search_music(q, limit)
    return {"query": q, "results": results, "count": len(results)}

@app.post("/api/play")
async def add_to_playlist(video_url: str = Form(...)):
    """Add song to continuous playlist"""
    try:
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL")

        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found")

        # Add to playlist
        track = {
            **video_info,
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'added_at': datetime.now().isoformat()
        }
        
        radio_state.playlist.append(track)
        
        # Ensure stream is running
        if not radio_state.is_streaming:
            await start_continuous_stream()
        
        base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
        
        return {
            "status": "added_to_playlist",
            "track": track,
            "position": len(radio_state.playlist),
            "radio_url": f"{base_url}/api/stream",
            "message": f"Added to playlist: {track['title']}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add to playlist error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream")
async def stream_radio():
    """
    Continuous radio stream endpoint.
    Serves audio from the LIVE buffer - all users hear same moment.
    Stream plays 24/7 regardless of listeners.
    """
    
    listener_id = id(asyncio.current_task())
    radio_state.listeners.add(listener_id)
    
    logger.info(f"👤 New listener connected (Total: {len(radio_state.listeners)})")
    
    async def generate_live_audio() -> AsyncIterator[bytes]:
        try:
            # Start from current buffer position (live join)
            buffer_position = max(0, len(radio_state.audio_chunks) - 10)  # Start near end
            
            while radio_state.is_streaming:
                # Get current buffer size
                async with radio_state.buffer_lock:
                    current_buffer_size = len(radio_state.audio_chunks)
                
                # If we have buffered audio ahead, send it
                if buffer_position < current_buffer_size:
                    async with radio_state.buffer_lock:
                        chunk = radio_state.audio_chunks[buffer_position]
                    yield chunk
                    buffer_position += 1
                else:
                    # Wait for new chunks
                    try:
                        await asyncio.wait_for(radio_state.chunk_event.wait(), timeout=1.0)
                        radio_state.chunk_event.clear()
                    except asyncio.TimeoutError:
                        # No new audio, send silence to keep connection alive
                        yield b'\x00' * 4096
                
        except asyncio.CancelledError:
            logger.info(f"👤 Listener disconnected (Total: {len(radio_state.listeners) - 1})")
            radio_state.listeners.discard(listener_id)
        except Exception as e:
            logger.error(f"Stream error: {e}")
            radio_state.listeners.discard(listener_id)
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
async def stop_stream():
    """Stop the continuous stream"""
    await stop_continuous_stream()
    radio_state.playlist.clear()
    return {"status": "stopped", "message": "Radio stream stopped"}

@app.post("/api/start")
async def start_stream():
    """Manually start the stream if stopped"""
    if not radio_state.playlist:
        await add_default_songs()
    
    await start_continuous_stream()
    return {
        "status": "started",
        "message": "Radio stream started",
        "playlist_size": len(radio_state.playlist)
    }

@app.post("/api/add-defaults")
async def add_defaults():
    """Manually add default songs to playlist"""
    await add_default_songs()
    return {
        "status": "success",
        "playlist_size": len(radio_state.playlist),
        "message": f"Added default songs. Playlist now has {len(radio_state.playlist)} tracks"
    }

@app.get("/api/status")
async def get_status():
    """Get current radio status"""
    uptime = None
    if radio_state.stream_started_at:
        uptime = (datetime.now() - radio_state.stream_started_at).total_seconds()
    
    return {
        "status": "playing" if radio_state.is_streaming else "stopped",
        "stream_active": radio_state.is_streaming,
        "current_track": radio_state.current_track,
        "playlist_size": len(radio_state.playlist),
        "active_listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_chunks),
        "uptime_seconds": uptime
    }

@app.get("/api/playlist")
async def get_playlist():
    """Get current playlist"""
    return {
        "current": radio_state.current_track,
        "queue": list(radio_state.playlist),
        "total": len(radio_state.playlist)
    }

@app.post("/api/skip")
async def skip_track():
    """Skip current track"""
    if not radio_state.is_streaming:
        raise HTTPException(status_code=400, detail="Nothing is playing")
    
    skipped = radio_state.current_track
    
    # Terminate FFmpeg to skip to next song
    if radio_state.stream_process and radio_state.stream_process.poll() is None:
        radio_state.stream_process.terminate()
        logger.info(f"⏭️ Skipped: {skipped['title']}")
    
    return {
        "status": "skipped",
        "skipped_track": skipped,
        "next_track": radio_state.playlist[0] if radio_state.playlist else None
    }

@app.get("/api/radio/url")
async def get_radio_url():
    base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
    return {
        "radio_url": f"{base_url}/api/stream",
        "status": "playing" if radio_state.is_streaming else "stopped",
        "current_track": radio_state.current_track['title'] if radio_state.current_track else None,
        "listeners": len(radio_state.listeners),
        "streaming_247": True
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "5.1.0",
        "streaming": radio_state.is_streaming,
        "listeners": len(radio_state.listeners),
        "buffer_chunks": len(radio_state.audio_chunks)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
