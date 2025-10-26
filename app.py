from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import yt_dlp
import os
from typing import Dict, List

app = FastAPI(title="Virus Music Radio API")

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

class MusicStreamer:
    def __init__(self):
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'extractaudio': True,
            'audioformat': 'mp3',
            'noplaylist': True,
            'quiet': True,
        }
    
    def search_youtube(self, query: str) -> List[Dict]:
        """Search YouTube for music"""
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                search_query = f"ytsearch5:{query}"
                info = ydl.extract_info(search_query, download=False)
                
                results = []
                for entry in info['entries']:
                    if entry:
                        results.append({
                            'id': entry['id'],
                            'title': entry['title'],
                            'url': entry['webpage_url'],
                            'duration': entry.get('duration', 0),
                            'thumbnail': entry.get('thumbnail'),
                            'uploader': entry.get('uploader')
                        })
                return results
        except Exception as e:
            print(f"Search error: {e}")
            return []
    
    def get_audio_stream(self, video_url: str) -> str:
        """Get direct audio stream URL from YouTube"""
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                return info['url']
        except Exception as e:
            print(f"Audio stream error: {e}")
            return None

music_streamer = MusicStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "1.0.0"
    }

@app.get("/api/search")
async def search_music(q: str):
    """Search for music on YouTube"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    results = music_streamer.search_youtube(q)
    return {"query": q, "results": results}

@app.post("/api/play")
async def play_music(video_url: str):
    """Play music and return stream information"""
    global current_track, player_status, current_audio_url
    
    try:
        # Get audio stream URL
        audio_url = music_streamer.get_audio_stream(video_url)
        if not audio_url:
            raise HTTPException(status_code=500, detail="Could not get audio stream")
        
        # Get track info
        with yt_dlp.YoutubeDL(music_streamer.ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            current_track = {
                'id': info['id'],
                'title': info['title'],
                'artist': info.get('uploader', 'Unknown Artist'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail'),
                'url': video_url
            }
        
        current_audio_url = audio_url
        player_status = "playing"
        
        # Get the public URL for this deployment
        base_url = os.getenv('RAILWAY_STATIC_URL', 'http://localhost:8000')
        stream_url = f"{base_url}/api/stream"
        
        return {
            "status": "playing", 
            "track": current_track,
            "stream_url": stream_url,
            "message": "Music is now streaming! Use !url to get the stream URL for Highrise."
        }
        
    except Exception as e:
        print(f"Play error: {e}")
        raise HTTPException(status_code=500, detail="Failed to start stream")

@app.get("/api/stream")
async def stream_audio():
    """Redirect to the actual audio stream (Highrise will follow this)"""
    global current_audio_url, player_status
    
    if player_status != "playing" or not current_audio_url:
        raise HTTPException(status_code=404, detail="No music currently playing")
    
    # Redirect Highrise to the actual audio stream URL
    return RedirectResponse(url=current_audio_url)

@app.post("/api/stop")
async def stop_music():
    """Stop music streaming"""
    global current_track, player_status, current_audio_url
    
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
    
    if player_status == "playing":
        return {
            "radio_url": stream_url,
            "status": "playing",
            "current_track": current_track['title'],
            "instructions": "Add this URL to your Highrise room music settings! The music will play automatically."
        }
    else:
        return {
            "radio_url": stream_url,
            "status": "stopped", 
            "instructions": "Play a song first using !play command, then music will stream to this URL"
        }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "player_status": player_status,
        "current_track": current_track['title'] if current_track else None
    }