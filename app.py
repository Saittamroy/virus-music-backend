from fastapi import FastAPI, HTTPException, Form, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
import aiohttp
import os
import asyncio
from typing import Dict, List, Optional, AsyncIterator
import re
import subprocess
from collections import deque
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Continuous Radio Stream API", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== GLOBAL STATE ====================
class RadioState:
    def __init__(self):
        self.playlist: deque = deque(maxlen=100)  # Queue of songs
        self.current_track: Optional[Dict] = None
        self.is_streaming = False
        self.stream_process: Optional[subprocess.Popen] = None
        self.audio_buffer = asyncio.Queue(maxsize=50)  # Circular buffer for audio chunks
        self.listeners = set()  # Track active listeners
        self.stream_started_at: Optional[datetime] = None
        
radio_state = RadioState()

# YouTube Data API configuration
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

            async with self.session.get(
                f"{self.base_url}/search",
                params=params
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

            async with self.session.get(
                f"{self.base_url}/videos",
                params=params
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
        await self.init_session()
        try:
            params = {
                'part': 'snippet,contentDetails',
                'id': video_id,
                'key': self.api_key
            }

            async with self.session.get(
                f"{self.base_url}/videos",
                params=params
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

async def get_audio_stream_with_ytdlp(youtube_url: str) -> Optional[str]:
    """Get audio stream URL using yt-dlp"""
    try:
        import yt_dlp
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'noplaylist': True,
            'no_warnings': True,
        }

        def extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(youtube_url, download=False)

        info = await asyncio.get_event_loop().run_in_executor(None, extract)
        
        if 'url' in info:
            return info['url']
        elif 'formats' in info:
            audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
            if audio_formats:
                best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
                return best_audio['url']
        
        return None
    except Exception as e:
        logger.error(f"yt-dlp error: {e}")
        return None

async def stream_audio_to_buffer(audio_url: str):
    """Stream audio data into circular buffer using ffmpeg"""
    try:
        # FFmpeg command to convert any audio to MP3 stream
        cmd = [
            'ffmpeg',
            '-i', audio_url,
            '-vn',  # No video
            '-acodec', 'libmp3lame',  # MP3 codec
            '-b:a', '128k',  # 128kbps bitrate
            '-ar', '44100',  # Sample rate
            '-ac', '2',  # Stereo
            '-f', 'mp3',  # Output format
            '-',  # Output to stdout
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=8192
        )

        radio_state.stream_process = process
        
        # Read and buffer audio chunks
        while radio_state.is_streaming and process.poll() is None:
            chunk = process.stdout.read(8192)
            if not chunk:
                break
            
            # Put in buffer (non-blocking, drop old if full)
            try:
                radio_state.audio_buffer.put_nowait(chunk)
            except asyncio.QueueFull:
                # Remove oldest chunk and add new one
                try:
                    radio_state.audio_buffer.get_nowait()
                    radio_state.audio_buffer.put_nowait(chunk)
                except:
                    pass
        
        process.terminate()
        logger.info(f"Finished streaming: {radio_state.current_track['title']}")
        
    except Exception as e:
        logger.error(f"Streaming error: {e}")
    finally:
        if process:
            process.terminate()

async def continuous_radio_loop():
    """Main radio loop - plays songs continuously"""
    logger.info("🎵 Starting continuous radio loop")
    radio_state.is_streaming = True
    radio_state.stream_started_at = datetime.now()
    
    while radio_state.is_streaming:
        try:
            # Wait for playlist to have songs
            while not radio_state.playlist and radio_state.is_streaming:
                await asyncio.sleep(2)
            
            if not radio_state.is_streaming:
                break
            
            # Get next track
            track = radio_state.playlist.popleft()
            radio_state.current_track = track
            
            logger.info(f"▶️ Now playing: {track['title']}")
            
            # Get audio stream URL
            audio_url = await get_audio_stream_with_ytdlp(track['url'])
            
            if audio_url:
                # Stream this track to buffer
                await stream_audio_to_buffer(audio_url)
            else:
                logger.warning(f"⚠️ Could not get audio for: {track['title']}")
                await asyncio.sleep(1)
            
            # Track finished, move to next
            
        except Exception as e:
            logger.error(f"Radio loop error: {e}")
            await asyncio.sleep(1)
    
    logger.info("🛑 Radio loop stopped")

# Background task for continuous streaming
streaming_task: Optional[asyncio.Task] = None

async def start_continuous_stream():
    """Start the continuous radio stream"""
    global streaming_task
    
    if streaming_task and not streaming_task.done():
        logger.info("Stream already running")
        return
    
    # Clear buffer
    while not radio_state.audio_buffer.empty():
        try:
            radio_state.audio_buffer.get_nowait()
        except:
            break
    
    # Start streaming loop
    streaming_task = asyncio.create_task(continuous_radio_loop())
    logger.info("✅ Continuous stream started")

async def stop_continuous_stream():
    """Stop the continuous radio stream"""
    global streaming_task
    
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
    logger.info("🛑 Continuous stream stopped")

# ==================== API ENDPOINTS ====================

@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    await youtube_service.init_session()
    logger.info("🚀 API Started")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    await stop_continuous_stream()
    await youtube_service.close_session()
    logger.info("👋 API Shutdown")

@app.get("/")
async def root():
    return {
        "message": "Continuous Radio Stream API",
        "status": "online",
        "version": "5.0.0",
        "streaming": radio_state.is_streaming,
        "current_track": radio_state.current_track['title'] if radio_state.current_track else None,
        "playlist_size": len(radio_state.playlist),
        "active_listeners": len(radio_state.listeners)
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
        
        # Start stream if not running
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
    All users connect here and hear the same audio at the same time.
    """
    
    listener_id = id(asyncio.current_task())
    radio_state.listeners.add(listener_id)
    
    logger.info(f"👤 New listener connected. Total: {len(radio_state.listeners)}")
    
    async def generate_audio() -> AsyncIterator[bytes]:
        try:
            # Send live audio from buffer
            while True:
                try:
                    # Get audio chunk from circular buffer
                    chunk = await asyncio.wait_for(
                        radio_state.audio_buffer.get(),
                        timeout=5.0
                    )
                    yield chunk
                    
                except asyncio.TimeoutError:
                    # No audio in buffer, send silence
                    yield b'\x00' * 8192
                    
        except asyncio.CancelledError:
            logger.info(f"👤 Listener disconnected")
            radio_state.listeners.discard(listener_id)
        except Exception as e:
            logger.error(f"Stream generation error: {e}")
            radio_state.listeners.discard(listener_id)
    
    return StreamingResponse(
        generate_audio(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "icy-br": "128",
            "icy-name": "Virus Radio",
            "icy-genre": "Various",
        }
    )

@app.post("/api/stop")
async def stop_stream():
    """Stop the continuous stream"""
    await stop_continuous_stream()
    radio_state.playlist.clear()
    return {"status": "stopped", "message": "Radio stream stopped"}

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
    
    # Stop current ffmpeg process to trigger next song
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
    
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
        "listeners": len(radio_state.listeners)
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "5.0.0",
        "streaming": radio_state.is_streaming
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
