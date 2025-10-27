from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
import youtube_dl
import asyncio
import os
import requests
from typing import Dict, List, Optional
import re

app = FastAPI(title="Virus Music Radio API", version="3.0.0")

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

# youtube-dl configuration (more stable for streaming)
YDL_OPTS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(title)s.%(ext)s',
    'noplaylist': True,
    'quiet': False,
    'no_warnings': False,
    'forceurl': True,
    'nocheckcertificate': True,
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt',
}

class YouTubeStreamer:
    def __init__(self):
        pass
    
    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for music using youtube-dl"""
        try:
            print(f"🔍 Searching for: {query}")
            
            def sync_search():
                with youtube_dl.YoutubeDL(YDL_OPTS) as ydl:
                    info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                    return info.get('entries', [])
            
            results = await asyncio.get_event_loop().run_in_executor(None, sync_search)
            formatted_results = []
            
            for video in results:
                if video:
                    formatted_results.append({
                        'id': video['id'],
                        'title': video['title'],
                        'url': video['webpage_url'],
                        'duration': video.get('duration', 0),
                        'thumbnail': video.get('thumbnail', ''),
                        'artist': video.get('uploader', 'Unknown Artist'),
                        'view_count': video.get('view_count', 0),
                        'source': 'youtube'
                    })
            
            print(f"✅ Found {len(formatted_results)} results")
            return formatted_results
            
        except Exception as e:
            print(f"❌ Search error: {e}")
            return []
    
    async def get_audio_stream_url(self, youtube_url: str) -> Optional[str]:
        """Get direct audio stream URL using multiple methods"""
        try:
            print(f"🎵 Getting audio URL for: {youtube_url}")
            
            # Method 1: Try youtube-dl first
            try:
                def extract_info():
                    with youtube_dl.YoutubeDL(YDL_OPTS) as ydl:
                        return ydl.extract_info(youtube_url, download=False)
                
                info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
                
                if 'url' in info:
                    print(f"✅ Method 1 - Got direct URL via youtube-dl")
                    return info['url']
                
                # Try to find best format
                if 'formats' in info:
                    # Sort by quality and get the best audio
                    audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                    if audio_formats:
                        # Prefer formats with both audio and video (they often work better)
                        best_format = max(audio_formats, key=lambda x: (
                            x.get('filesize', 0) or 
                            x.get('quality', 0) or 
                            0
                        ))
                        print(f"✅ Method 1 - Got format URL: {best_format.get('format_note', 'unknown')}")
                        return best_format['url']
                        
            except Exception as e:
                print(f"❌ Method 1 failed: {e}")
            
            # Method 2: Try yt-dlp as fallback
            try:
                import yt_dlp
                ydlp_opts = {
                    'format': 'bestaudio/best',
                    'extractaudio': True,
                    'noplaylist': True,
                    'quiet': True,
                }
                
                def ytdlp_extract():
                    with yt_dlp.YoutubeDL(ydlp_opts) as ydl:
                        return ydl.extract_info(youtube_url, download=False)
                
                info = await asyncio.get_event_loop().run_in_executor(None, ytdlp_extract)
                if 'url' in info:
                    print(f"✅ Method 2 - Got URL via yt-dlp fallback")
                    return info['url']
                    
            except Exception as e:
                print(f"❌ Method 2 failed: {e}")
            
            # Method 3: Use external service as last resort
            fallback_url = await self.get_fallback_stream(youtube_url)
            if fallback_url:
                print(f"✅ Method 3 - Using fallback service")
                return fallback_url
            
            print("❌ All methods failed to get audio URL")
            return None
            
        except Exception as e:
            print(f"❌ Audio URL extraction error: {e}")
            return None
    
    async def get_fallback_stream(self, youtube_url: str) -> Optional[str]:
        """Use external services as fallback"""
        try:
            # Try using y2mate or similar services
            video_id = self.extract_video_id(youtube_url)
            if video_id:
                # Try different proxy services
                services = [
                    f"https://api.douyin.wtf/api/stream?url={youtube_url}",
                    f"https://ytdl.squidproxy.xyz/{video_id}",
                    f"https://youtube.squidproxy.xyz/{video_id}",
                ]
                
                for service in services:
                    try:
                        response = requests.get(service, timeout=10)
                        if response.status_code == 200:
                            data = response.json()
                            if 'url' in data:
                                return data['url']
                    except:
                        continue
                        
        except Exception as e:
            print(f"❌ Fallback service error: {e}")
        
        return None
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL"""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&]+)',
            r'youtube\.com/embed/([^?]+)',
            r'youtube\.com/v/([^?]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def get_video_info(self, youtube_url: str) -> Optional[Dict]:
        """Get video information"""
        try:
            def extract_info():
                with youtube_dl.YoutubeDL(YDL_OPTS) as ydl:
                    return ydl.extract_info(youtube_url, download=False)
            
            info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
            
            return {
                'id': info['id'],
                'title': info['title'],
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'artist': info.get('uploader', 'Unknown Artist'),
                'view_count': info.get('view_count', 0),
                'description': info.get('description', '')[:100] + '...' if info.get('description') else ''
            }
        except Exception as e:
            print(f"❌ Video info error: {e}")
            return None

# Initialize streamer
music_streamer = YouTubeStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "3.0.0 - youtube-dl + Multiple Fallbacks",
        "endpoints": {
            "search": "/api/search?q=query",
            "play": "POST /api/play",
            "stream": "/api/stream",
            "status": "/api/status",
            "stop": "POST /api/stop",
            "test": "/api/test-stream"
        }
    }

@app.get("/api/search")
async def search_music(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=20)):
    """Search for music on YouTube"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    print(f"🎵 API Search: {q}")
    results = await music_streamer.search_music(q, limit)
    
    return {
        "query": q, 
        "results": results, 
        "count": len(results),
        "message": f"Found {len(results)} tracks"
    }

@app.post("/api/play")
async def play_music(video_url: str = Form(...)):
    """Play music from YouTube URL"""
    global current_track, player_status, current_audio_url
    
    try:
        print(f"🎵 Play request received: {video_url}")
        
        # Get video information
        video_info = await music_streamer.get_video_info(video_url)
        if not video_info:
            raise HTTPException(status_code=404, detail="Video not found or unavailable")
        
        # Get audio stream URL using multiple methods
        audio_url = await music_streamer.get_audio_stream_url(video_url)
        if not audio_url:
            raise HTTPException(status_code=404, detail="Could not get audio stream from any method")
        
        print(f"🎵 Audio URL obtained successfully")
        
        # Set current track
        current_track = {
            'id': video_info['id'],
            'title': video_info['title'],
            'artist': video_info['artist'],
            'duration': video_info['duration'],
            'thumbnail': video_info['thumbnail'],
            'url': audio_url,
            'source': 'youtube'
        }
        
        current_audio_url = audio_url
        player_status = "playing"
        
        return {
            "status": "playing", 
            "track": current_track,
            "stream_url": "https://virus-music-backend-production.up.railway.app/api/stream",
            "message": f"🎵 Now playing: {current_track['title']} by {current_track['artist']}",
            "stream_method": "Proxied audio stream"
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
        # Return a silent audio stream instead of error
        silent_audio = b'\x00' * 1024  # Minimal silent audio
        return StreamingResponse(
            iter([silent_audio]),
            media_type="audio/mpeg",
            headers={"Content-Type": "audio/mpeg"}
        )
    
    try:
        print(f"🎵 Proxying YouTube audio stream...")
        
        def generate():
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*',
                    'Accept-Encoding': 'identity',
                    'Range': 'bytes=0-',
                }
                
                response = requests.get(current_audio_url, stream=True, timeout=30, headers=headers)
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
        "service": "YouTube Music Streaming",
        "version": "3.0.0"
    }

@app.get("/api/test-stream")
async def test_stream():
    """Test if streaming is working with a known good audio file"""
    test_url = "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"
    
    def generate():
        response = requests.get(test_url, stream=True)
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                yield chunk
    
    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers={"Content-Type": "audio/mpeg"}
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)