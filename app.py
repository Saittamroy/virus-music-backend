from fastapi import FastAPI, HTTPException, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
import requests
import os
import asyncio
from typing import Dict, List, Optional, Set
import re
import aiohttp
import time
import json

app = FastAPI(title="Virus Music Radio API", version="4.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Global state (shared)
# -------------------------
current_track: Optional[Dict] = None
player_status: str = "stopped"   # "playing" or "stopped"
current_audio_url: Optional[str] = None

# A FIFO queue for user-requested tracks (each item is dict with keys: id, title, url, requested_by)
request_queue: List[Dict] = []

# Internal control objects
_player_task: Optional[asyncio.Task] = None
_player_loop_should_run = True

# For broadcasting to connected /api/stream clients
# Each client gets its own asyncio.Queue of bytes; the player loop puts bytes into all client queues
_client_queues: Set[asyncio.Queue] = set()
_client_queues_lock = asyncio.Lock()

# Event used to interrupt current playback (for immediate skip when a requested song is queued)
_skip_current = asyncio.Event()

# YouTube API config
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

# Predefined random Bollywood/club YouTube URLs (replace with your curated list)
BOLLYWOOD_RANDOM_TRACKS = [
    # Example YouTube watch URLs — replace or extend with your favorites
    "https://www.youtube.com/watch?v=DWcJFNfaw9c",
    "https://www.youtube.com/watch?v=kJQP7kiw5Fk",
    "https://www.youtube.com/watch?v=2Vv-BfVoq4g",
    "https://www.youtube.com/watch?v=jt2pF0rI8V0",
    # Add many more to ensure continuous variety
]

# -----------------------------
# YouTubeAPIService (keeps same interface)
# -----------------------------
class YouTubeAPIService:
    def __init__(self):
        self.api_key = YOUTUBE_API_KEY
        self.base_url = YOUTUBE_API_URL

    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
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
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: requests.get(f"{self.base_url}/search", params=params, timeout=10)
            )
            if response.status_code != 200:
                print(f"❌ YouTube API Error: {response.status_code} - {response.text}")
                return []
            data = response.json()
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
        except Exception as e:
            print(f"❌ YouTube API search error: {e}")
            return []

    async def get_video_duration(self, video_id: str) -> int:
        try:
            params = {
                'part': 'contentDetails',
                'id': video_id,
                'key': self.api_key
            }
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: requests.get(f"{self.base_url}/videos", params=params, timeout=10)
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('items'):
                    duration_str = data['items'][0]['contentDetails']['duration']
                    return self.parse_duration(duration_str)
            return 0
        except Exception as e:
            print(f"❌ Duration fetch error: {e}")
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
            response = await asyncio.get_event_loop().run_in_executor(
                None, lambda: requests.get(f"{self.base_url}/videos", params=params, timeout=10)
            )
            if response.status_code == 200:
                data = response.json()
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
        try:
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                return None
            print(f"🎵 Getting audio stream for video: {video_id}")

            # Method 1: yt-dlp
            try:
                import yt_dlp
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'quiet': True,
                    'noplaylist': True,
                }
                def extract_info():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(youtube_url, download=False)
                info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
                # yt-dlp returns 'url' for direct format entries or 'formats' list; handle both
                if isinstance(info, dict):
                    # Prefer direct url key if present
                    if 'url' in info:
                        print("✅ Got stream via yt-dlp (info['url'])")
                        return info['url']
                    # else try formats
                    formats = info.get('formats') or []
                    if formats:
                        # pick best audio with an url
                        for f in reversed(formats):
                            if f.get('acodec') != 'none' and f.get('url'):
                                print("✅ Got stream via yt-dlp (formats)")
                                return f['url']
            except Exception as e:
                print(f"❌ yt-dlp method failed: {e}")

            # Method 2: proxy
            proxy_url = await self.get_proxy_stream(video_id)
            if proxy_url:
                return proxy_url

            # fallback audio
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"
        except Exception as e:
            print(f"❌ Audio stream error: {e}")
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"

    async def get_proxy_stream(self, video_id: str) -> Optional[str]:
        services = [
            f"https://api.douyin.wtf/api/stream?url=https://www.youtube.com/watch?v={video_id}",
        ]
        for service in services:
            try:
                response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: requests.get(service, timeout=10)
                )
                if response.status_code == 200:
                    data = response.json()
                    if 'url' in data:
                        print(f"✅ Got stream via proxy: {service}")
                        return data['url']
            except Exception as e:
                print(f"❌ Proxy {service} failed: {e}")
                continue
        return None

    def extract_video_id(self, url: str) -> Optional[str]:
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&?/]+)',
            r'youtube\.com/embed/([^?]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

# Initialize youtube_service
youtube_service = YouTubeAPIService()

# -----------------------------
# Helper functions for broadcasting
# -----------------------------
async def register_client_queue() -> asyncio.Queue:
    q = asyncio.Queue(maxsize=32)
    async with _client_queues_lock:
        _client_queues.add(q)
    return q

async def unregister_client_queue(q: asyncio.Queue):
    async with _client_queues_lock:
        if q in _client_queues:
            _client_queues.remove(q)

async def broadcast_chunk(chunk: bytes):
    """Push the same chunk to every connected client queue (drop if queue full)."""
    async with _client_queues_lock:
        for q in list(_client_queues):
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                # If a client's buffer is full, drop this chunk for that client to avoid blocking
                pass

# -----------------------------
# Player loop (background)
# -----------------------------
async def player_loop():
    """
    Continuously runs:
      - If request_queue has items: play them FIFO.
      - Else: pick random from BOLLYWOOD_RANDOM_TRACKS and play.
    For each 'play' it fetches stream URL (via youtube_service.get_audio_stream_url)
    then performs an aiohttp GET and reads chunks, broadcasting into client queues.
    Supports interruption by setting _skip_current event.
    """
    global current_track, current_audio_url, player_status, _skip_current

    print("🔁 Player loop starting...")
    while _player_loop_should_run:
        try:
            next_item = None
            if request_queue:
                # Pop next requested song (FIFO)
                next_item = request_queue.pop(0)
                print(f"▶️ Playing requested track: {next_item.get('title')}")
            else:
                # Choose random track from pool
                import random
                random_url = random.choice(BOLLYWOOD_RANDOM_TRACKS)
                next_item = {
                    'id': None,
                    'title': f"Random Track ({random_url.split('v=')[-1]})",
                    'url': random_url,
                    'artist': 'Bollywood Mix',
                    'source': 'random'
                }
                print(f"▶️ No queued requests — playing random: {next_item['url']}")

            # Resolve audio stream url
            play_url = await youtube_service.get_audio_stream_url(next_item['url'])
            if not play_url:
                print("❌ Could not resolve audio stream URL, skipping to next")
                continue

            # Set current metadata
            current_audio_url = play_url
            current_track = {
                'id': next_item.get('id'),
                'title': next_item.get('title'),
                'artist': next_item.get('artist', 'Unknown'),
                'duration': next_item.get('duration', 0),
                'thumbnail': next_item.get('thumbnail'),
                'url': play_url,
                'source': next_item.get('source', 'youtube')
            }
            player_status = "playing"
            _skip_current.clear()

            # Upstream fetch & broadcast loop using aiohttp streaming
            # We'll open the upstream connection and stream its content out to all listeners.
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        # don't request range — treat as live stream
                    }
                    async with session.get(current_audio_url, headers=headers) as upstream_resp:
                        print(f"🔗 Upstream response status: {upstream_resp.status} content-type: {upstream_resp.headers.get('content-type')}")
                        if upstream_resp.status != 200 and upstream_resp.status != 206:
                            print("❌ Upstream returned non-OK status — skipping track")
                        else:
                            # Read chunks until upstream ends or _skip_current is set
                            async for chunk in upstream_resp.content.iter_chunked(8192):
                                if _skip_current.is_set():
                                    # Immediately stop reading further chunks for this track
                                    print("⏭️ Skip signal set — stopping current track fetch")
                                    break
                                if not chunk:
                                    await asyncio.sleep(0.01)
                                    continue
                                # broadcast chunk to clients (non-blocking)
                                await broadcast_chunk(chunk)
                            # finished with this track
            except Exception as e:
                print(f"❌ Upstream fetch error: {e}")

            # After track ended or skipped, short gap then continue loop
            # Clear current_audio_url/metadata only if no queued tracks and next action is stop
            # For continuous play we simply loop to next track
            current_audio_url = None
            current_track = None
            player_status = "stopped" if not request_queue and not BOLLYWOOD_RANDOM_TRACKS else "stopped"
            # small sleep to avoid busy-loop
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            print("🔴 Player task cancelled")
            break
        except Exception as e:
            print(f"❌ Player loop error: {e}")
            await asyncio.sleep(1)

# Start the player loop as background task on startup
@app.on_event("startup")
async def startup_event():
    global _player_task, _player_loop_should_run
    _player_loop_should_run = True
    _player_task = asyncio.create_task(player_loop())
    print("✅ Player background task started")

@app.on_event("shutdown")
async def shutdown_event():
    global _player_task, _player_loop_should_run
    _player_loop_should_run = False
    if _player_task:
        _player_task.cancel()
    print("⛔ Shutting down player loop")

# -----------------------------
# API endpoints (kept shape compatible)
# -----------------------------

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API",
        "status": "online",
        "version": "4.2.0",
        "endpoints": {
            "search": "/api/search?q=query",
            "play": "POST /api/play",
            "stream": "/api/stream",
            "status": "/api/status",
            "stop": "POST /api/stop"
        }
    }

@app.get("/api/search")
async def search_music(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=20)):
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube API key not configured")
    print(f"🎵 API Search: {q}")
    results = await youtube_service.search_music(q, limit)
    return {
        "query": q,
        "results": results,
        "count": len(results),
        "message": f"Found {len(results)} tracks via YouTube API"
    }

@app.post("/api/play")
async def play_music(video_url: str = Form(...), request: Request = None):
    """
    If a track is playing:
      - Add requested track to request_queue and return queue position.
    If nothing is playing:
      - Add track to queue and signal player loop to start it immediately.
    Returns 'radio_url' for compatibility with your bot.
    """
    global request_queue, _skip_current, player_status

    try:
        print(f"🎵 Play request received: {video_url}")
        # Extract id and info if possible
        video_id = youtube_service.extract_video_id(video_url)
        video_info = None
        if video_id and YOUTUBE_API_KEY:
            video_info = await youtube_service.get_video_info(video_id)

        track_meta = {
            'id': video_id,
            'title': (video_info['title'] if video_info else video_url),
            'url': video_url,
            'artist': (video_info['artist'] if video_info else 'Unknown'),
            'duration': (video_info['duration'] if video_info else 0),
            'thumbnail': (video_info.get('thumbnail') if video_info else None),
            'requested_at': time.time(),
        }

        # If already playing, queue the request and return position
        if player_status == "playing" and current_track is not None:
            request_queue.append(track_meta)
            position = len(request_queue)
            print(f"➕ Track queued at position {position}")
            return JSONResponse({
                "status": "queued",
                "position": position,
                "track": track_meta,
                "radio_url": f"{request.url.scheme}://{request.client.host}{request.url.path}".replace("/api/play", "/api/stream") if request else "/api/stream",
                "message": f"Track queued at position {position}"
            })

        # If not playing -> append and signal player loop (it will pick it up)
        request_queue.append(track_meta)
        # If random is currently playing, instruct skip to interrupt random and start requested immediately
        _skip_current.set()
        print("▶️ Requested track appended; signaled player loop to start it immediately")

        # Return radio_url for bot compatibility
        # Try to return full URL if request context available, else fallback static stream url
        radio_url = f"{request.url.scheme}://{request.client.host}{request.url.path}".replace("/api/play", "/api/stream") if request else "https://virus-music-backend-production.up.railway.app/api/stream"
        return {
            "status": "playing",
            "track": track_meta,
            "radio_url": radio_url,
            "message": f"🎵 Your track will play next"
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to play: {str(e)}")

@app.get("/api/stream")
async def stream_audio(request: Request):
    """
    Clients connect here and receive the live stream. Each client receives the
    broadcasted chunk stream (they do not re-request the upstream from the beginning).
    """
    global player_status

    # Register a per-client queue
    q = await register_client_queue()
    print(f"🔌 Client connected, total clients: {len(_client_queues)}")

    async def client_generator():
        try:
            # While connected, yield chunks as they arrive
            while True:
                # If client disconnects, Request.is_disconnected is True (fastapi starlette)
                if await request.is_disconnected():
                    break
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield chunk
                except asyncio.TimeoutError:
                    # If nothing for a while, send a tiny silent keepalive so clients won't hang
                    yield b''  # zero-length chunk as keepalive
                    continue
        finally:
            await unregister_client_queue(q)
            print(f"🔌 Client disconnected, remaining clients: {len(_client_queues)}")

    # Keep the media_type audio/mpeg as before
    return StreamingResponse(client_generator(), media_type="audio/mpeg", headers={
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache"
    })

@app.post("/api/stop")
async def stop_music():
    """
    Called by bot for stop/skip. Behavior:
      - If there are more songs in request_queue -> skip current and play next
      - Else -> stop playback (player loop will continue playing random tracks unless stopped)
    """
    global request_queue, _skip_current, player_status, current_track

    try:
        if request_queue:
            # skip current and start next requested immediately
            _skip_current.set()
            # player_loop will pop next item automatically
            print("⏭️ Skip requested — there is a queued track, skipping to next")
            return {"status": "playing", "message": "Skipped to next track"}
        else:
            # no queued songs — stop playback (pause broadcasting upstream, keep stream endpoint alive)
            # We signal skip_current to stop upstream fetch and leave player to pick next (it will choose random)
            _skip_current.set()
            # clear current_track and mark stopped so /api/status shows stopped until random starts
            current_track = None
            player_status = "stopped"
            print("⏹️ Stop requested — no queued tracks, stopping current playback")
            return {"status": "stopped", "message": "Playback stopped"}
    except Exception as e:
        print(f"❌ Stop error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status")
async def get_player_status():
    return {
        "status": player_status,
        "current_track": current_track,
        "stream_active": player_status == "playing",
        "queued": len(request_queue)
    }

@app.get("/api/radio/url")
async def get_radio_url():
    stream_url = "https://virus-music-backend-production.up.railway.app/api/stream"
    return {
        "radio_url": stream_url,
        "status": player_status,
        "current_track": current_track['title'] if current_track else 'No track playing',
        "artist": current_track['artist'] if current_track else 'None',
        "instructions": "Add this URL to Highrise room music settings!"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "player_status": player_status,
        "service": "YouTube Data API Streaming",
        "version": "4.2.0"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
