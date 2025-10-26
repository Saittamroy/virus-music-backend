from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import subprocess
import os
from typing import Dict, List

app = FastAPI(title="Virus Music Radio API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Radio configuration - will be set by Railway
RADIO_CONFIG = {
    'host': os.getenv('RAILWAY_STATIC_URL', 'localhost').replace('https://', '').replace('http://', ''),
    'port': '8001',
    'password': 'hackme',
    'mount': '/highrise'
}

# Global state
current_stream_process = None
current_track = None
player_status = "stopped"

class RadioStreamer:
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
    
    def start_stream(self, video_url: str) -> bool:
        """Start streaming YouTube audio to Icecast"""
        global current_stream_process, player_status, current_track
        
        try:
            # Stop existing stream
            if current_stream_process:
                current_stream_process.terminate()
                current_stream_process = None
            
            # Get stream URL and info
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                audio_url = info['url']
                
                # Store track info
                current_track = {
                    'id': info['id'],
                    'title': info['title'],
                    'artist': info.get('uploader', 'Unknown'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail'),
                    'url': video_url
                }
            
            # Build FFmpeg command for Icecast streaming
            ffmpeg_cmd = [
                'ffmpeg',
                '-re',
                '-i', audio_url,
                '-acodec', 'libmp3lame',
                '-ab', '128k',
                '-ac', '2',
                '-content_type', 'audio/mpeg',
                '-f', 'mp3',
                f'icecast://source:{RADIO_CONFIG["password"]}@{RADIO_CONFIG["host"]}:{RADIO_CONFIG["port"]}{RADIO_CONFIG["mount"]}'
            ]
            
            print(f"🎧 Starting stream to Icecast...")
            
            # Start streaming process
            current_stream_process = subprocess.Popen(ffmpeg_cmd)
            player_status = "playing"
            return True
            
        except Exception as e:
            print(f"Stream error: {e}")
            player_status = "error"
            return False
    
    def stop_stream(self):
        """Stop current radio stream"""
        global current_stream_process, player_status, current_track
        
        if current_stream_process:
            current_stream_process.terminate()
            current_stream_process = None
        
        current_track = None
        player_status = "stopped"

radio_streamer = RadioStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "radio_url": f"http://{RADIO_CONFIG['host']}:{RADIO_CONFIG['port']}{RADIO_CONFIG['mount']}",
        "instructions": "Use /api/radio/url to get your Highrise stream URL"
    }

@app.get("/api/search")
async def search_music(q: str):
    """Search for music on YouTube"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    results = radio_streamer.search_youtube(q)
    return {"query": q, "results": results}

@app.post("/api/play")
async def play_music(video_url: str):
    """Play music on radio stream"""
    success = radio_streamer.start_stream(video_url)
    
    if success:
        return {
            "status": "playing", 
            "track": current_track,
            "radio_url": f"http://{RADIO_CONFIG['host']}:{RADIO_CONFIG['port']}{RADIO_CONFIG['mount']}"
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to start stream")

@app.post("/api/stop")
async def stop_music():
    """Stop radio stream"""
    radio_streamer.stop_stream()
    return {"status": "stopped"}

@app.get("/api/status")
async def get_player_status():
    """Get current player status"""
    return {
        "status": player_status,
        "current_track": current_track,
        "radio_url": f"http://{RADIO_CONFIG['host']}:{RADIO_CONFIG['port']}{RADIO_CONFIG['mount']}",
        "stream_active": current_stream_process is not None
    }

@app.get("/api/radio/url")
async def get_radio_url():
    """Get the radio stream URL for Highrise"""
    return {
        "radio_url": f"http://{RADIO_CONFIG['host']}:{RADIO_CONFIG['port']}{RADIO_CONFIG['mount']}",
        "status": player_status,
        "instructions": "Add this URL to your Highrise room music settings"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "radio_streaming": current_stream_process is not None,
        "current_track": current_track
    }