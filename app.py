from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
import yt_dlp
import asyncio
import os
import requests
from typing import Dict, List, Optional
import uuid
import io

app = FastAPI(title="Virus Music Radio API", version="2.1.0")

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
current_audio_data = None

# yt-dlp configuration for better audio extraction
YDL_OPTS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': 'downloads/%(title)s.%(ext)s',
    'noplaylist': True,
    'quiet': False,
    'no_warnings': False,
    'forceurl': True,
    'cookiefile': 'cookies.txt',  # Optional: helps with age-restricted content
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
            
            # Use different options for better stream compatibility
            stream_opts = {
                'format': 'bestaudio/best',
                'extractaudio': True,
                'audioformat': 'mp3',
                'noplaylist': True,
                'forceurl': True,
                'cookiefile': 'cookies.txt',
            }
            
            def extract_info():
                with yt_dlp.YoutubeDL(stream_opts) as ydl:
                    return ydl.extract_info(youtube_url, download=False)
            
            info = await asyncio.get_event_loop().run_in_executor(None, extract_info)
            
            # Try to get the direct URL
            if 'url' in info:
                print(f"✅ Got direct audio URL: {info['url'][:100]}...")
                return info['url']
            
            # Try different format selection
            if 'formats' in info:
                # Prefer formats that are known to work with streaming
                preferred_formats = [
                    f for f in info['formats'] 
                    if f.get('acodec') != 'none' and 
                    any(x in f.get('format_note', '').lower() for x in ['medium', 'high', 'audio'])
                ]
                
                if preferred_formats:
                    best_audio = max(preferred_formats, key=lambda x: x.get('quality', 0))
                    print(f"✅ Got preferred audio format URL")
                    return best_audio['url']
            
            print("❌ No suitable audio URL found")
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

    async def test_stream_url(self, stream_url: str) -> bool:
        """Test if the stream URL is actually playable"""
        try:
            print(f"🔍 Testing stream URL: {stream_url[:100]}...")
            
            def test_request():
                response = requests.get(stream_url, stream=True, timeout=10)
                return response.status_code == 200 and 'audio' in response.headers.get('content-type', '')
            
            return await asyncio.get_event_loop().run_in_executor(None, test_request)
        except Exception as e:
            print(f"❌ Stream test failed: {e}")
            return False

# Initialize streamer
music_streamer = YouTubeStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "2.1.0 - Fixed Streaming",
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
        
        # Test if the stream is actually playable
        is_playable = await music_streamer.test_stream_url(audio_url)
        if not is_playable:
            print("⚠️ Stream URL might not be playable by Highrise")
        
        # Set current track
        current_track = {
            'id': video_info['id'],
            'title': video_info['title'],
            'artist': video_info['artist'],
            'duration': video_info['duration'],
            'thumbnail': video_info['thumbnail'],
            'url': audio_url,
            'source': 'youtube',
            'playable': is_playable
        }
        
        current_audio_url = audio_url
        player_status = "playing"
        
        return {
            "status": "playing", 
            "track": current_track,
            "stream_url": f"https://virus-music-backend-production.up.railway.app/api/stream",
            "message": f"🎵 Now playing: {current_track['title']} by {current_track['artist']}",
            "stream_test": "playable" if is_playable else "may not be compatible"
        }
        
    except Exception as e:
        print(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to play: {str(e)}")

@app.get("/api/stream")
async def stream_audio():
    """Stream audio - PROXY the YouTube audio stream with proper headers"""
    global current_audio_url, player_status
    
    print(f"🎵 Stream request received - Status: {player_status}")
    
    if player_status != "playing" or not current_audio_url:
        raise HTTPException(status_code=404, detail="No active stream. Please play a song first.")
    
    try:
        print(f"🎵 Proxying stream: {current_audio_url[:100]}...")
        
        def generate():
            response = requests.get(current_audio_url, stream=True, timeout=30)
            response.raise_for_status()
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        
        # Return as a proper audio stream with correct headers
        return StreamingResponse(
            generate(),
            media_type="audio/mpeg",
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
            }
        )
        
    except Exception as e:
        print(f"❌ Stream proxy error: {e}")
        raise HTTPException(status_code=500, detail="Streaming failed")

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
        "instructions": "Add this URL to Highrise room music settings!",
        "note": "This URL streams the currently playing song"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "player_status": player_status,
        "service": "YouTube Music Streaming",
        "version": "2.1.0"
    }

# Test endpoint to verify streaming works
@app.get("/api/test-stream")
async def test_stream():
    """Test if streaming is working"""
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