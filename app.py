from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
import os
import asyncio
from typing import Dict, List, Optional
import re
import random
from collections import deque
import time

app = FastAPI(title="Virus Music Radio API", version="4.2.0")

# Allow all origins (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
current_track = None
player_status = "stopped"
current_audio_url = None
play_queue = deque()
is_random_playing = False
random_playlist = []

# YouTube Data API configuration
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

# Bollywood club songs for random playback
BOLYWOOD_CLUB_SONGS = [
    "Bollywood club mix 2024",
    "Bollywood dance songs 2024",
    "Bollywood remix songs",
    "Bollywood party songs",
    "Bollywood club hits"
]

# -----------------------------
# 🎵 YouTube API Service Class
# -----------------------------
class YouTubeAPIService:
    def __init__(self):
        self.api_key = YOUTUBE_API_KEY
        self.base_url = YOUTUBE_API_URL

    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for music videos using YouTube Data API."""
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
        """Get YouTube video duration in seconds."""
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
        """Convert ISO 8601 duration (e.g. PT4M13S) to seconds."""
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return 0
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    async def get_video_info(self, video_id: str) -> Optional[Dict]:
        """Fetch detailed video info."""
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
        """Try multiple methods to get audio stream URL."""
        try:
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                return None

            print(f"🎵 Getting audio stream for: {video_id}")

            # Try yt-dlp first
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
                if 'url' in info:
                    print("✅ Stream via yt-dlp")
                    return info['url']
            except Exception as e:
                print(f"❌ yt-dlp failed: {e}")

            # Try proxy service fallback
            proxy_url = await self.get_proxy_stream(video_id)
            if proxy_url:
                return proxy_url

            # Fallback audio
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"

        except Exception as e:
            print(f"❌ Audio stream error: {e}")
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"

    async def get_proxy_stream(self, video_id: str) -> Optional[str]:
        """Fallback proxy audio stream services."""
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
                        print(f"✅ Stream via proxy: {service}")
                        return data['url']
            except Exception as e:
                print(f"❌ Proxy failed: {e}")
                continue

        return None

    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from various URL formats."""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&?/]+)',
            r'youtube\.com/embed/([^?]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

# Initialize YouTube service
youtube_service = YouTubeAPIService()

# -----------------------------
# 🎧 Queue Management Functions
# -----------------------------

async def add_to_queue(video_url: str, requested_by: str = "Unknown"):
    """Add song to queue"""
    try:
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            return {"success": False, "error": "Invalid URL"}

        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            return {"success": False, "error": "Video not found"}

        queue_item = {
            **video_info,
            "url": video_url,
            "requested_by": requested_by,
            "added_at": time.time()
        }

        play_queue.append(queue_item)
        position = len(play_queue)
        
        return {
            "success": True, 
            "position": position,
            "track": queue_item
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

async def play_next_in_queue():
    """Play next song in queue"""
    global current_track, player_status, current_audio_url, is_random_playing
    
    if play_queue:
        next_track = play_queue.popleft()
        try:
            audio_url = await youtube_service.get_audio_stream_url(next_track['url'])
            if audio_url:
                current_track = {**next_track, "audio_url": audio_url}
                current_audio_url = audio_url
                player_status = "playing"
                is_random_playing = False
                print(f"🎵 Now playing from queue: {current_track['title']}")
                return True
        except Exception as e:
            print(f"Queue play error: {e}")
    
    # Start random if no queue
    await start_random_playback()
    return False

async def start_random_playback():
    """Start random Bollywood songs"""
    global current_track, player_status, current_audio_url, is_random_playing, random_playlist
    
    if not random_playlist:
        random_playlist = BOLYWOOD_CLUB_SONGS.copy()
        random.shuffle(random_playlist)
    
    if not random_playlist:
        return
    
    search_query = random_playlist.pop(0)
    if not random_playlist:
        random_playlist = BOLYWOOD_CLUB_SONGS.copy()
        random.shuffle(random_playlist)
    
    try:
        print(f"🎲 Searching random: {search_query}")
        results = await youtube_service.search_music(search_query, limit=1)
        if results:
            random_track = results[0]
            audio_url = await youtube_service.get_audio_stream_url(random_track['url'])
            
            if audio_url:
                current_track = {
                    **random_track,
                    "audio_url": audio_url,
                    "requested_by": "Auto DJ",
                    "is_random": True
                }
                current_audio_url = audio_url
                player_status = "playing"
                is_random_playing = True
                print(f"🎲 Now playing random: {current_track['title']}")
    except Exception as e:
        print(f"Random playback error: {e}")

async def skip_current_track():
    """Skip current track"""
    global current_track, player_status, current_audio_url
    
    if play_queue:
        await play_next_in_queue()
        return {"success": True, "message": "Skipped to next in queue"}
    elif is_random_playing:
        await start_random_playback()
        return {"success": True, "message": "Skipped to next random song"}
    else:
        current_track = None
        player_status = "stopped"
        current_audio_url = None
        return {"success": True, "message": "Stopped - no more songs"}

# Start random playback on startup
@app.on_event("startup")
async def startup_event():
    await start_random_playback()

# -----------------------------
# 🎧 FastAPI Endpoints
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
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube API key not configured")
    results = await youtube_service.search_music(q, limit)
    return {"query": q, "results": results, "count": len(results)}

@app.post("/api/play")
async def play_music(video_url: str = Form(...), requested_by: str = Form("Unknown")):
    global current_track, player_status, current_audio_url, is_random_playing
    
    try:
        print(f"🎵 Play request from {requested_by}: {video_url}")
        
        # If random is playing, stop it and play requested song immediately
        if is_random_playing:
            play_queue.clear()
            is_random_playing = False
        
        # If something is playing, add to queue
        if player_status == "playing" and current_track:
            result = await add_to_queue(video_url, requested_by)
            if result["success"]:
                return {
                    "status": "queued",
                    "position": result["position"],
                    "track": result["track"],
                    "message": f"🎵 Added to queue (position {result['position']}): {result['track']['title']}"
                }
            else:
                raise HTTPException(status_code=400, detail=result["error"])
        
        # Play immediately
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL")

        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found")

        audio_url = await youtube_service.get_audio_stream_url(video_url)
        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")

        current_track = {
            **video_info,
            "url": video_url,
            "audio_url": audio_url,
            "requested_by": requested_by,
            "source": 'youtube_api'
        }
        current_audio_url = audio_url
        player_status = "playing"
        is_random_playing = False

        return {
            "status": "playing",
            "track": current_track,
            "stream_url": "https://virus-music-backend-production.up.railway.app/api/stream",
            "message": f"🎵 Now playing: {current_track['title']} by {current_track['artist']}"
        }

    except Exception as e:
        print(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# New endpoints for queue management
@app.post("/api/queue")
async def add_to_queue_endpoint(video_url: str = Form(...), requested_by: str = Form("Unknown")):
    result = await add_to_queue(video_url, requested_by)
    if result["success"]:
        return {
            "status": "queued",
            "position": result["position"],
            "track": result["track"],
            "queue_length": len(play_queue)
        }
    else:
        raise HTTPException(status_code=400, detail=result["error"])

@app.post("/api/skip")
async def skip_track():
    result = await skip_current_track()
    return result

@app.get("/api/queue")
async def get_queue_status():
    return {
        "current_track": current_track,
        "queue": list(play_queue),
        "queue_length": len(play_queue),
        "is_random_playing": is_random_playing
    }

@app.get("/api/stream")
async def stream_audio():
    global current_audio_url, player_status

    if player_status != "playing" or not current_audio_url:
        if not await play_next_in_queue():
            await start_random_playback()
        
        if not current_audio_url:
            silent_audio = b'\x00' * 1024
            return StreamingResponse(iter([silent_audio]), media_type="audio/mpeg")

    try:
        def generate():
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0',
                    'Accept': '*/*',
                    'Range': 'bytes=0-',
                }
                response = requests.get(current_audio_url, stream=True, timeout=30, headers=headers)
                response.raise_for_status()
                
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
                    else:
                        break
                
                # Song ended, play next
                asyncio.create_task(play_next_in_queue())
                
            except Exception as e:
                print(f"Stream error: {e}")
                asyncio.create_task(play_next_in_queue())
                yield b'\x00' * 8192

        return StreamingResponse(generate(), media_type="audio/mpeg", headers={"Access-Control-Allow-Origin": "*"})

    except Exception as e:
        print(f"Stream proxy error: {e}")
        return StreamingResponse(iter([b'\x00' * 1024]), media_type="audio/mpeg")

@app.post("/api/stop")
async def stop_music():
    global current_track, player_status, current_audio_url, play_queue, is_random_playing
    current_track = None
    player_status = "stopped"
    current_audio_url = None
    play_queue.clear()
    is_random_playing = False
    print("🛑 Music stopped")
    return {"status": "stopped"}

@app.get("/api/status")
async def get_player_status():
    return {
        "status": player_status,
        "current_track": current_track,
        "stream_active": player_status == "playing"
    }

@app.get("/api/radio/url")
async def get_radio_url():
    return {
        "radio_url": "https://virus-music-backend-production.up.railway.app/api/stream",
        "status": player_status,
        "current_track": current_track['title'] if current_track else 'No track playing',
        "artist": current_track['artist'] if current_track else 'None',
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "4.2.0"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
