from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
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

app = FastAPI(title="Live Music Radio Stream API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class RadioState:
    def __init__(self):
        self.playlist: deque = deque(maxlen=100)
        self.current_track: Optional[Dict] = None
        self.is_streaming = False
        self.is_paused = False
        self.stream_process: Optional[subprocess.Popen] = None
        self.audio_chunks: deque = deque(maxlen=500)
        self.listeners = set()
        self.stream_started_at: Optional[datetime] = None
        self.buffer_lock = asyncio.Lock()
        self.chunk_event = asyncio.Event()
        self.skip_requested = False
        
radio_state = RadioState()

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

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
            if not self.api_key:
                return []
            
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
            if not self.api_key:
                return {
                    'id': video_id,
                    'title': f'Video {video_id}',
                    'duration': 0,
                    'thumbnail': '',
                    'artist': 'Unknown',
                }
            
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

async def get_audio_stream_with_ytdlp(youtube_url: str) -> Optional[str]:
    try:
        import yt_dlp
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': False,
            'no_warnings': False,
            'noplaylist': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android_creator', 'android', 'web'],
                    'skip': ['hls', 'dash'],
                }
            },
        }
        
        cookie_file = os.getenv('YOUTUBE_COOKIES_FILE')
        if cookie_file and os.path.exists(cookie_file):
            ydl_opts['cookiefile'] = cookie_file
            logger.info(f"Using cookie file: {cookie_file}")
        elif cookie_file:
            logger.warning(f"Cookie file not found: {cookie_file}")

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
    try:
        logger.info(f"🎵 Starting FFmpeg stream for: {radio_state.current_track['title']}")
        
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
            'pipe:1',
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=8192
        )

        radio_state.stream_process = process
        
        while radio_state.is_streaming and not radio_state.is_paused and not radio_state.skip_requested and process.poll() is None:
            chunk = process.stdout.read(8192)
            if not chunk:
                break
            
            async with radio_state.buffer_lock:
                radio_state.audio_chunks.append(chunk)
                radio_state.chunk_event.set()
            
            await asyncio.sleep(0)
        
        if process.poll() is None:
            process.terminate()
            
        logger.info(f"✅ Finished streaming: {radio_state.current_track['title']}")
        
    except Exception as e:
        logger.error(f"Streaming error: {e}")
    finally:
        if process and process.poll() is None:
            process.terminate()

async def add_default_songs():
    logger.info("📋 Adding default tracks to playlist...")
    
    default_songs = [
        "kJQP7kiw5Fk",
        "fJ9rUzIMcZQ",
        "RgKAFK5djSk",
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

async def continuous_radio_loop():
    logger.info("🎵 Starting continuous radio loop (24/7 mode)")
    radio_state.is_streaming = True
    radio_state.stream_started_at = datetime.now()
    
    if not radio_state.playlist:
        await add_default_songs()
    
    while radio_state.is_streaming:
        try:
            while not radio_state.playlist and radio_state.is_streaming:
                logger.warning("⚠️ Playlist empty, waiting for songs...")
                await asyncio.sleep(5)
            
            if not radio_state.is_streaming:
                break
            
            if radio_state.is_paused:
                await asyncio.sleep(1)
                continue
            
            track = radio_state.playlist.popleft()
            radio_state.current_track = track
            radio_state.skip_requested = False
            
            logger.info(f"▶️ NOW PLAYING: {track['title']} (Listeners: {len(radio_state.listeners)})")
            
            audio_url = await get_audio_stream_with_ytdlp(track['url'])
            
            if audio_url:
                await stream_audio_to_buffer(audio_url)
            else:
                logger.warning(f"⚠️ Could not get audio for: {track['title']}")
                await asyncio.sleep(2)
            
            logger.info(f"✅ Track completed. Queue size: {len(radio_state.playlist)}")
            
        except Exception as e:
            logger.error(f"Radio loop error: {e}")
            await asyncio.sleep(1)
    
    logger.info("🛑 Radio loop stopped")

streaming_task: Optional[asyncio.Task] = None

async def start_continuous_stream():
    global streaming_task
    
    if streaming_task and not streaming_task.done():
        logger.info("✅ Stream already running")
        return
    
    radio_state.audio_chunks.clear()
    radio_state.is_paused = False
    
    streaming_task = asyncio.create_task(continuous_radio_loop())
    logger.info("✅ Continuous stream started (24/7 mode)")

async def stop_continuous_stream():
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

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 API Starting...")
    await youtube_service.init_session()
    
    await asyncio.sleep(1)
    
    if not radio_state.playlist:
        await add_default_songs()
    
    if radio_state.playlist:
        await start_continuous_stream()
        logger.info("🚀 API Started - Radio streaming 24/7")
    else:
        logger.warning("⚠️ Could not add default songs. Use /api/request to add music.")
        logger.info("🚀 API Started - Waiting for songs")

@app.on_event("shutdown")
async def shutdown_event():
    await stop_continuous_stream()
    await youtube_service.close_session()
    logger.info("👋 API Shutdown")

@app.get("/")
async def root():
    return {
        "message": "Live Music Radio Stream API",
        "status": "online",
        "version": "1.0.0",
        "streaming": radio_state.is_streaming,
        "paused": radio_state.is_paused,
        "current_track": radio_state.current_track['title'] if radio_state.current_track else None,
        "playlist_size": len(radio_state.playlist),
        "active_listeners": len(radio_state.listeners),
        "buffer_size": len(radio_state.audio_chunks),
        "stream_url": "/api/stream"
    }

@app.get("/api/search")
async def search_music(q: str = Query(...), limit: int = Query(5, ge=1, le=20)):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube API not configured")
    results = await youtube_service.search_music(q, limit)
    return {"query": q, "results": results, "count": len(results)}

@app.post("/api/request")
async def request_song(query: str = Form(...)):
    try:
        video_id = youtube_service.extract_video_id(query)
        
        if not video_id:
            if YOUTUBE_API_KEY:
                results = await youtube_service.search_music(query, 1)
                if results:
                    video_id = results[0]['id']
                    video_info = results[0]
                else:
                    raise HTTPException(status_code=404, detail="No songs found")
            else:
                raise HTTPException(status_code=400, detail="Invalid YouTube URL and API not configured")
        else:
            video_info = await youtube_service.get_video_info(video_id)
            if not video_info:
                raise HTTPException(status_code=404, detail="Video not found")

        track = {
            **video_info,
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'added_at': datetime.now().isoformat()
        }
        
        radio_state.playlist.append(track)
        
        if not radio_state.is_streaming:
            await start_continuous_stream()
        
        return {
            "status": "added_to_queue",
            "track": track,
            "position": len(radio_state.playlist),
            "message": f"Added to queue: {track['title']}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Request song error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/skip")
async def skip_song():
    if not radio_state.current_track:
        raise HTTPException(status_code=400, detail="No song playing")
    
    logger.info(f"⏭️ Skipping: {radio_state.current_track['title']}")
    
    radio_state.skip_requested = True
    
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
    
    return {
        "status": "skipped",
        "skipped_track": radio_state.current_track['title'],
        "next_in_queue": len(radio_state.playlist)
    }

@app.post("/api/pause")
async def pause_playback():
    if radio_state.is_paused:
        raise HTTPException(status_code=400, detail="Already paused")
    
    logger.info("⏸️ Pausing playback")
    radio_state.is_paused = True
    
    if radio_state.stream_process:
        radio_state.stream_process.terminate()
    
    return {
        "status": "paused",
        "message": "Broadcast paused"
    }

@app.post("/api/resume")
async def resume_playback():
    if not radio_state.is_paused:
        raise HTTPException(status_code=400, detail="Not paused")
    
    logger.info("▶️ Resuming playback")
    radio_state.is_paused = False
    
    return {
        "status": "resumed",
        "message": "Broadcast resumed"
    }

@app.get("/api/nowplaying")
async def now_playing():
    if not radio_state.current_track:
        return {"playing": False, "message": "Nothing playing"}
    
    return {
        "playing": True,
        "paused": radio_state.is_paused,
        "track": radio_state.current_track,
        "listeners": len(radio_state.listeners),
        "queue_size": len(radio_state.playlist)
    }

@app.get("/api/queue")
async def get_queue():
    return {
        "current": radio_state.current_track,
        "queue": list(radio_state.playlist),
        "total": len(radio_state.playlist)
    }

@app.get("/api/stream")
async def stream_radio():
    listener_id = id(asyncio.current_task())
    radio_state.listeners.add(listener_id)
    
    logger.info(f"👤 New listener connected (Total: {len(radio_state.listeners)})")
    
    async def generate_live_audio() -> AsyncIterator[bytes]:
        try:
            chunk_index = max(0, len(radio_state.audio_chunks) - 10)
            
            while radio_state.is_streaming:
                if radio_state.is_paused:
                    await asyncio.sleep(0.5)
                    continue
                
                if chunk_index < len(radio_state.audio_chunks):
                    chunk = radio_state.audio_chunks[chunk_index]
                    chunk_index += 1
                    yield chunk
                else:
                    radio_state.chunk_event.clear()
                    try:
                        await asyncio.wait_for(radio_state.chunk_event.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
        finally:
            radio_state.listeners.discard(listener_id)
            logger.info(f"👤 Listener disconnected (Remaining: {len(radio_state.listeners)})")
    
    return StreamingResponse(
        generate_live_audio(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
