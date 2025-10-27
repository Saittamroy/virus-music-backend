from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
import os
import asyncio
from typing import Dict, List, Optional
import re

app = FastAPI(title="Virus Music Radio API", version="4.1.0")

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

# YouTube Data API Configuration
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"

class YouTubeAPIService:
    def __init__(self):
        self.api_key = YOUTUBE_API_KEY
        self.base_url = YOUTUBE_API_URL
    
    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for music using YouTube Data API"""
        try:
            print(f"🔍 Searching via YouTube API: {query}")
            
            params = {
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'videoCategoryId': '10',  # Music category
                'maxResults': limit,
                'key': self.api_key
            }
            
            response = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: requests.get(f"{self.base_url}/search", params=params, timeout=10)
            )
            
            if response.status_code != 200:
                print(f"❌ YouTube API Error: {response.status_code} - {response.text}")
                return []
            
            data = response.json()
            results = []
            
            for item in data.get('items', []):
                video_id = item['id']['videoId']
                snippet = item['snippet']
                
                # Get video duration
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
        """Get video duration using YouTube Data API"""
        try:
            params = {
                'part': 'contentDetails',
                'id': video_id,
                'key': self.api_key
            }
            
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: requests.get(f"{self.base_url}/videos", params=params, timeout=10)
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('items'):
                    duration_str = data['items'][0]['contentDetails']['duration']
                    # Convert ISO 8601 duration to seconds
                    return self.parse_duration(duration_str)
            
            return 0
        except Exception as e:
            print(f"❌ Duration fetch error: {e}")
            return 0
    
    def parse_duration(self, duration: str) -> int:
        """Parse ISO 8601 duration to seconds"""
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return 0
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        return hours * 3600 + minutes * 60 + seconds
    
    async def get_video_info(self, video_id: str) -> Optional[Dict]:
        """Get video information using YouTube API"""
        try:
            params = {
                'part': 'snippet,contentDetails',
                'id': video_id,
                'key': self.api_key
            }
            
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: requests.get(f"{self.base_url}/videos", params=params, timeout=10)
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
        """Get audio stream using external services"""
        try:
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                return None
            
            print(f"🎵 Getting audio stream for video: {video_id}")
            
            # Method 1: Try yt-dlp with cookies
            try:
                import yt_dlp
                
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'extractaudio': True,
                    'noplaylist': True,
                    'quiet': True,
                }
                
                def extract_info():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(youtube_url, download=False)
                
                info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
                if 'url' in info:
                    print("✅ Got stream via yt-dlp")
                    return info['url']
                    
            except Exception as e:
                print(f"❌ yt-dlp method failed: {e}")
            
            # Method 2: Use external proxy services
            proxy_url = await self.get_proxy_stream(video_id)
            if proxy_url:
                return proxy_url
            
            # Method 3: Fallback to working audio
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"
            
        except Exception as e:
            print(f"❌ Audio stream error: {e}")
            return "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"
    
    async def get_proxy_stream(self, video_id: str) -> Optional[str]:
        """Try various proxy services for audio streaming"""
        services = [
            f"https://api.douyin.wtf/api/stream?url=https://www.youtube.com/watch?v={video_id}",
        ]
        
        for service in services:
            try:
                response = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    lambda url: requests.get(url, timeout=10), 
                    service
                )
                if response.status_code == 200:
                    data = response.json()
                    if 'url' in data:
                        print(f"✅ Got stream via proxy: {service}")
                        return data['url']
            except:
                continue
        
        return None
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL"""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&]+)',
            r'youtube\.com/embed/([^?]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

# Initialize YouTube API service
youtube_service = YouTubeAPIService()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "4.1.0 - Fixed YouTube API",
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
    """Search for music using YouTube Data API"""
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
async def play_music(video_url: str = Form(...)):
    """Play music from YouTube URL"""
    global current_track, player_status, current_audio_url
    
    try:
        print(f"🎵 Play request received: {video_url}")
        
        # Extract video ID for API lookup
        video_id = youtube_service.extract_video_id(video_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Invalid YouTube URL")
        
        # Get video info using YouTube API
        video_info = await youtube_service.get_video_info(video_id)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found via YouTube API")
        
        # Get audio stream URL
        audio_url = await youtube_service.get_audio_stream_url(video_url)
        if not audio_url:
            raise HTTPException(status_code=404, detail="Could not get audio stream")
        
        print(f"🎵 Audio stream obtained: {audio_url[:100]}..." if len(audio_url) > 100 else f"🎵 Audio stream obtained: {audio_url}")
        
        # Set current track
        current_track = {
            'id': video_id,
            'title': video_info['title'],
            'artist': video_info['artist'],
            'duration': video_info['duration'],
            'thumbnail': video_info['thumbnail'],
            'url': audio_url,
            'source': 'youtube_api'
        }
        
        current_audio_url = audio_url
        player_status = "playing"
        
        return {
            "status": "playing", 
            "track": current_track,
            "stream_url": "https://virus-music-backend-production.up.railway.app/api/stream",
            "message": f"🎵 Now playing: {current_track['title']} by {current_track['artist']}"
        }
        
    except Exception as e:
        print(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to play: {str(e)}")

@app.get("/api/stream")
async def stream_audio():
    """Stream audio - PROXY the audio with proper headers"""
    global current_audio_url, player_status
    
    print(f"🎵 Stream request received - Status: {player_status}")
    
    if player_status != "playing" or not current_audio_url:
        # Return silent audio instead of error
        silent_audio = b'\x00' * 1024
        return StreamingResponse(
            iter([silent_audio]),
            media_type="audio/mpeg",
            headers={"Content-Type": "audio/mpeg"}
        )
    
    try:
        print(f"🎵 Proxying audio stream...")
        
        def generate():
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*',
                    'Range': 'bytes=0-',
                }
                
                response = requests.get(current_audio_url, stream=True, timeout=30, headers=headers)
                print(f"🎵 Stream response status: {response.status_code}")
                print(f"🎵 Stream content type: {response.headers.get('content-type')}")
                
                response.raise_for_status()
                
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
                        
            except Exception as e:
                print(f"❌ Stream chunk error: {e}")
                # Yield silent audio on error
                yield b'\x00' * 8192
        
        return StreamingResponse(
            generate(),
            media_type="audio/mpeg",
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*",
            }
        )
        
    except Exception as e:
        print(f"❌ Stream proxy error: {e}")
        # Return silent audio instead of error
        silent_audio = b'\x00' * 1024
        return StreamingResponse(
            iter([silent_audio]),
            media_type="audio/mpeg"
        )

@app.post("/api/stop")
async def stop_music():
    """Stop music streaming"""
    global current_track, player_status, current_audio_url
    
    print(f"🎵 Stop request received")
    
    current_track = None
    player_status = "stopped"
    current_audio_url = None
    
    return {"status": "stopped", "message": "Music stopped"}

@app.get("/api/status")
async def get_player_status():
    """Get current player status"""
    return {
        "status": player_status,
        "current_track": current_track,
        "stream_active": player_status == "playing"
    }

@app.get("/api/radio/url")
async def get_radio_url():
    """Get the radio stream URL for Highrise"""
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
        "version": "4.1.0"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)