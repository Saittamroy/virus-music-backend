from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
import yt_dlp
import asyncio
import os
from typing import Dict, List, Optional
import uuid

app = FastAPI(title="Virus Music Radio API", version="2.0.0")

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

# yt-dlp configuration
YDL_OPTS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': 'downloads/%(title)s.%(ext)s',
    'noplaylist': True,
    'quiet': False,
    'no_warnings': False,
}

class YouTubeStreamer:
    def __init__(self):
        pass
    
    async def search_music(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for music using yt-dlp"""
        try:
            print(f"🔍 Searching for: {query}")
            
            def sync_search():
                with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
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
        """Get direct audio stream URL for playback"""
        try:
            print(f"🎵 Getting audio URL for: {youtube_url}")
            
            def extract_info():
                with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                    return ydl.extract_info(youtube_url, download=False)
            
            info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
            
            # Get the best audio URL
            if 'url' in info:
                print(f"✅ Got direct audio URL")
                return info['url']
            
            # Fallback: find best audio format
            if 'formats' in info:
                audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                if audio_formats:
                    best_audio = max(audio_formats, key=lambda x: x.get('quality', 0))
                    print(f"✅ Got audio format URL")
                    return best_audio['url']
            
            return None
            
        except Exception as e:
            print(f"❌ Audio URL extraction error: {e}")
            return None
    
    async def get_video_info(self, youtube_url: str) -> Optional[Dict]:
        """Get video information"""
        try:
            def extract_info():
                with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
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
        "version": "2.0.0 - YouTube Streaming",
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
        
        # Get audio stream URL
        audio_url = await music_streamer.get_audio_stream_url(video_url)
        if not audio_url:
            raise HTTPException(status_code=404, detail="Could not get audio stream")
        
        print(f"🎵 Audio URL obtained: {audio_url[:100]}...")
        
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
        
        # Get stream URL for Highrise
        base_url = os.getenv('RAILWAY_STATIC_URL', 'http://localhost:8000')
        stream_url = f"{base_url}/api/stream"
        
        return {
            "status": "playing", 
            "track": current_track,
            "stream_url": stream_url,
            "message": f"🎵 Now playing: {current_track['title']} by {current_track['artist']}"
        }
        
    except Exception as e:
        print(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to play: {str(e)}")

@app.get("/api/stream")
async def stream_audio():
    """Stream audio - redirects to YouTube audio stream"""
    global current_audio_url, player_status
    
    print(f"🎵 Stream request received - Status: {player_status}")
    
    if player_status == "playing" and current_audio_url:
        print(f"🎵 Redirecting to audio stream: {current_audio_url[:100]}...")
        return RedirectResponse(url=current_audio_url)
    else:
        # Return error instead of default stream
        raise HTTPException(status_code=404, detail="No active stream. Please play a song first.")

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
    base_url = os.getenv('RAILWAY_STATIC_URL', 'http://localhost:8000')
    stream_url = f"{base_url}/api/stream"
    
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
        "version": "2.0.0"
    }

# Additional utility endpoints
@app.get("/api/info")
async def get_video_info(url: str = Query(...)):
    """Get video information without playing"""
    info = await music_streamer.get_video_info(url)
    if not info:
        raise HTTPException(status_code=404, detail="Video not found")
    return info

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)