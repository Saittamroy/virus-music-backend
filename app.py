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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state for live broadcasting
class RadioState:
    def __init__(self):
        self.current_track = None
        self.player_status = "stopped"
        self.current_audio_url = None
        self.audio_buffer = deque(maxlen=2000)  # Larger buffer
        self.listeners = set()
        self.buffer_lock = asyncio.Lock()
        self.chunk_event = asyncio.Event()
        self.stream_process = None
        self.is_streaming = False
        self.stream_task = None

radio_state = RadioState()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage aiohttp session lifecycle"""
    global http_session
    http_session = aiohttp.ClientSession()
    yield
    await http_session.close()
    if radio_state.stream_process:
        radio_state.stream_process.terminate()

app = FastAPI(
    title="Virus Music Radio API",
    version="4.4.0",
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
        try:
            import yt_dlp
            return True
        except ImportError:
            logger.warning("⚠️ yt-dlp not installed")
            return False

    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        try:
            logger.info(f"🔍 Searching: {query}")
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

                logger.info(f"✅ Found {len(results)} results")
                return results

        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            return []

    async def get_video_duration(self, video_id: str) -> int:
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
                        }
            return None
        except Exception as e:
            logger.error(f"❌ Video info error: {e}")
            return None

    async def get_audio_stream_url(self, youtube_url: str) -> Optional[str]:
        try:
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                return None
    
            logger.info(f"🎵 Getting stream for: {video_id}")
    
            # Try yt-dlp with cookies/anti-bot measures
            if self.has_ytdlp:
                try:
                    import yt_dlp
                    ydl_opts = {
                        'format': 'bestaudio/best',
                        'quiet': True,
                        'noplaylist': True,
                        'extract_flat': False,
                        'extractor_args': {
                            'youtube': {
                                'player_client': ['android', 'web'],
                            }
                        },
                        'http_headers': {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        },
                        # Add retry for bot detection
                        'retries': 3,
                        'fragment_retries': 3,
                        'skip_download': True,
                    }
    
                    def extract_info():
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            return ydl.extract_info(youtube_url, download=False)
    
                    info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
                    
                    if 'url' in info:
                        logger.info("✅ Stream via yt-dlp (direct)")
                        return info['url']
                    elif 'formats' in info:
                        audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                        if audio_formats:
                            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
                            logger.info("✅ Stream via yt-dlp (format)")
                            return best_audio['url']
                            
                except Exception as e:
                    logger.error(f"❌ yt-dlp failed: {e}")
                    # Fall through to Invidious
    
            # Try Invidious as fallback
            invidious_url = await self.get_invidious_stream(video_id)
            if invidious_url:
                return invidious_url
    
            return None
    
        except Exception as e:
            logger.error(f"❌ Stream error: {e}")
            return None

    async def get_invidious_stream(self, video_id: str) -> Optional[str]:
        invidious_instances = [
            "https://invidious.privacydev.net",
            "https://inv.tux.pizza",
            "https://invidious.lunar.icu",
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
                            logger.info(f"✅ Stream via Invidious")
                            return best_audio['url']
            except Exception as e:
                logger.error(f"❌ Invidious {instance} failed: {e}")
                continue

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

async def stream_audio_to_buffer(audio_url: str):
    """Stream audio continuously to buffer - PROPERLY."""
    try:
        logger.info("🎵 Starting FFmpeg continuous stream")
        
        # FFmpeg with reconnection and error resilience
        cmd = [
            'ffmpeg',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
            '-i', audio_url,
            '-vn',
            '-acodec', 'libmp3lame',
            '-b:a', '128k',
            '-ar', '44100',
            '-ac', '2',
            '-f', 'mp3',
            '-bufsize', '256k',
            'pipe:1'
        ]

        # Use asyncio subprocess for proper async handling
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        radio_state.stream_process = process
        radio_state.is_streaming = True
        
        logger.info("▶️ FFmpeg started, reading audio chunks...")
        
        chunk_count = 0
        
        # Read audio continuously until process ends or stopped
        while radio_state.is_streaming and process.returncode is None:
            try:
                # Read chunk asynchronously with timeout
                chunk = await asyncio.wait_for(process.stdout.read(8192), timeout=5.0)
                
                if not chunk:
                    logger.warning("⚠️ No more data from FFmpeg (EOF)")
                    break
                
                chunk_count += 1
                
                # Add to circular buffer
                async with radio_state.buffer_lock:
                    radio_state.audio_buffer.append(chunk)
                    radio_state.chunk_event.set()
                
                # Log progress every 100 chunks
                if chunk_count % 100 == 0:
                    logger.info(f"📊 Buffered {chunk_count} chunks, buffer size: {len(radio_state.audio_buffer)}")
                
            except asyncio.TimeoutError:
                logger.warning("⚠️ Timeout reading from FFmpeg")
                continue
            except Exception as e:
                logger.error(f"❌ Chunk read error: {e}")
                break
        
        logger.info(f"✅ Streaming completed. Total chunks: {chunk_count}")
        
    except Exception as e:
        logger.error(f"❌ Stream to buffer error: {e}")
    finally:
        radio_state.is_streaming = False
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        
        # Check stderr for FFmpeg errors
        if process.stderr:
            try:
                stderr_output = await process.stderr.read()
                if stderr_output:
                    logger.error(f"FFmpeg stderr: {stderr_output.decode('utf-8', errors='ignore')[:500]}")
            except Exception as e:
                logger.error(f"Could not read stderr: {e}")

# API Endpoints
@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API",
        "status": "online",
        "version": "4.4.0",
        "listeners": len(radio_state.listeners),
        "streaming": radio_state.is_streaming,
        "buffer_size": len(radio_state.audio_buffer),
        "current_track": radio_state.current_track['title'] if radio_state.current_track else None
    }

@app.get("/api/search")
async def search_music(q: str = Query(...), limit: int = Query(10, ge=1, le=20)):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube API not configured")
    results = await youtube_service.search_music(q, limit)
    return {"query": q, "results": results, "count": len(results)}

@app.post("/api/play")
async def play_music(video_url: str = Form(...)):
    try:
        logger.info(f"🎵 Play request: {video_url}")
        
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL")

        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found")

        audio_url = await youtube_service.get_audio_stream_url(f"https://www.youtube.com/watch?v={video_id}")
        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")

        # Stop previous stream
        if radio_state.stream_process:
            logger.info("🛑 Stopping previous stream")
            radio_state.is_streaming = False
            radio_state.stream_process.terminate()
            try:
                radio_state.stream_process.wait(timeout=2)
            except:
                radio_state.stream_process.kill()
            
            # Wait for old stream to fully stop
            await asyncio.sleep(1)
        
        # Cancel old stream task
        if radio_state.stream_task and not radio_state.stream_task.done():
            radio_state.stream_task.cancel()
            try:
                await radio_state.stream_task
            except asyncio.CancelledError:
                pass
        
        # Clear buffer for new track
        async with radio_state.buffer_lock:
            radio_state.audio_buffer.clear()
        
        # Update state
        radio_state.current_track = {**video_info, "url": audio_url}
        radio_state.current_audio_url = audio_url
        radio_state.player_status = "playing"

        # Start new stream in background
        radio_state.stream_task = asyncio.create_task(stream_audio_to_buffer(audio_url))

        base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
        
        logger.info(f"✅ Now playing: {video_info['title']}")
        
        return {
            "status": "playing",
            "track": radio_state.current_track,
            "radio_url": f"{base_url}/api/stream",
            "listeners": len(radio_state.listeners),
            "message": f"🎵 Now playing: {video_info['title']}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream")
async def stream_audio():
    """Live broadcast - all listeners hear same audio at same time."""
    
    listener_id = id(asyncio.current_task())
    radio_state.listeners.add(listener_id)
    
    logger.info(f"👤 Listener joined (Total: {len(radio_state.listeners)})")
    
    async def generate_live_audio() -> AsyncIterator[bytes]:
        try:
            # Start from near the end of buffer (live position)
            async with radio_state.buffer_lock:
                current_position = max(0, len(radio_state.audio_buffer) - 50)
            
            logger.info(f"📍 Starting at buffer position {current_position}")
            
            silence_count = 0
            
            while radio_state.player_status == "playing":
                async with radio_state.buffer_lock:
                    buffer_size = len(radio_state.audio_buffer)
                
                # Send available chunks
                if current_position < buffer_size:
                    async with radio_state.buffer_lock:
                        chunk = radio_state.audio_buffer[current_position]
                    
                    yield chunk
                    current_position += 1
                    silence_count = 0
                    
                else:
                    # Wait for new chunks
                    try:
                        await asyncio.wait_for(radio_state.chunk_event.wait(), timeout=2.0)
                        radio_state.chunk_event.clear()
                    except asyncio.TimeoutError:
                        # Send silence if no new audio
                        yield b'\x00' * 4096
                        silence_count += 1
                        
                        # If too much silence, stream might have ended
                        if silence_count > 5:
                            logger.warning("⚠️ No audio for 10s, stopping listener")
                            break
                
        except asyncio.CancelledError:
            logger.info(f"👤 Listener left (Total: {len(radio_state.listeners) - 1})")
        except Exception as e:
            logger.error(f"❌ Stream error: {e}")
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
            "icy-name": "Virus Radio",
        }
    )

@app.post("/api/stop")
async def stop_music():
    logger.info("🛑 Stopping music")
    
    radio_state.is_streaming = False
    
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
        try:
            await asyncio.wait_for(radio_state.stream_process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            radio_state.stream_process.kill()
            await radio_state.stream_process.wait()
    
    radio_state.current_track = None
    radio_state.player_status = "stopped"
    radio_state.current_audio_url = None
    
    async with radio_state.buffer_lock:
        radio_state.audio_buffer.clear()
    
    return {"status": "stopped", "listeners": len(radio_state.listeners)}

@app.get("/api/status")
async def get_player_status():
    return {
        "status": radio_state.player_status,
        "current_track": radio_state.current_track,
        "stream_active": radio_state.is_streaming,
        "listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_buffer),
    }

@app.get("/api/radio/url")
async def get_radio_url():
    base_url = os.getenv("BASE_URL", "https://virus-music-backend-production.up.railway.app")
    return {
        "radio_url": f"{base_url}/api/stream",
        "status": radio_state.player_status,
        "current_track": radio_state.current_track['title'] if radio_state.current_track else None,
        "listeners": len(radio_state.listeners),
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "4.4.0",
        "streaming": radio_state.is_streaming,
        "listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_buffer),
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
