from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import os
import asyncio
from typing import Dict, List
import yt_dlp
import aiohttp

app = FastAPI(title="AzuraCast Radio API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Radio configuration
ICECAST_CONFIG = {
    'host': '0.0.0.0',
    'port': '8001',
    'password': 'hackme',
    'mount': '/radio'
}

# Global state
current_stream_process = None
current_track = None
player_status = "stopped"

class AzuraCastStreamer:
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
                return results[:3]
        except Exception as e:
            print(f"Search error: {e}")
            return self._get_fallback_songs(query)
    
    def _get_fallback_songs(self, query: str) -> List[Dict]:
        """Fallback songs"""
        fallback_songs = [
            {
                'id': 'kJQP7kiw5Fk',
                'title': 'Despacito',
                'url': 'https://www.youtube.com/watch?v=kJQP7kiw5Fk',
                'duration': 280,
                'thumbnail': 'https://i.ytimg.com/vi/kJQP7kiw5Fk/hqdefault.jpg',
                'uploader': 'Luis Fonsi'
            },
            {
                'id': 'JGwWNGJdvx8',
                'title': 'Shape of You', 
                'url': 'https://www.youtube.com/watch?v=JGwWNGJdvx8',
                'duration': 234,
                'thumbnail': 'https://i.ytimg.com/vi/JGwWNGJdvx8/hqdefault.jpg',
                'uploader': 'Ed Sheeran'
            }
        ]
        return fallback_songs
    
    def start_radio_stream(self, video_url: str) -> bool:
        """Start streaming to Icecast radio"""
        global current_stream_process, player_status, current_track
        
        try:
            # Stop existing stream
            if current_stream_process:
                current_stream_process.terminate()
                current_stream_process = None
            
            # Get audio URL and track info
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                audio_url = info['url']
                
                current_track = {
                    'id': info['id'],
                    'title': info['title'],
                    'artist': info.get('uploader', 'Unknown'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail'),
                    'url': video_url
                }
            
            # Start Icecast server in background
            self._start_icecast()
            
            # Stream to Icecast using FFmpeg
            ffmpeg_cmd = [
                'ffmpeg',
                '-re',
                '-i', audio_url,
                '-acodec', 'libmp3lame',
                '-ab', '128k',
                '-ac', '2',
                '-content_type', 'audio/mpeg',
                '-f', 'mp3',
                f'icecast://source:{ICECAST_CONFIG["password"]}@{ICECAST_CONFIG["host"]}:{ICECAST_CONFIG["port"]}{ICECAST_CONFIG["mount"]}'
            ]
            
            print(f"🎧 Starting radio stream: {' '.join(ffmpeg_cmd)}")
            current_stream_process = subprocess.Popen(ffmpeg_cmd)
            player_status = "playing"
            return True
            
        except Exception as e:
            print(f"Radio stream error: {e}")
            player_status = "error"
            return False
    
    def _start_icecast(self):
        """Start Icecast server in background"""
        try:
            # Create basic icecast config
            icecast_config = f"""
<icecast>
    <location>Radio Server</location>
    <admin>admin@localhost</admin>
    
    <limits>
        <clients>100</clients>
        <sources>5</sources>
        <threadpool>5</threadpool>
        <queue-size>524288</queue-size>
        <client-timeout>30</client-timeout>
        <header-timeout>15</header-timeout>
        <source-timeout>10</source-timeout>
    </limits>
    
    <authentication>
        <source-password>{ICECAST_CONFIG['password']}</source-password>
        <relay-password>{ICECAST_CONFIG['password']}</relay-password>
        <admin-user>admin</admin-user>
        <admin-password>{ICECAST_CONFIG['password']}</admin-password>
    </authentication>
    
    <hostname>{ICECAST_CONFIG['host']}</hostname>
    <listen-socket>
        <port>{ICECAST_CONFIG['port']}</port>
    </listen-socket>
    
    <fileserve>1</fileserve>
    <paths>
        <logdir>/var/log/icecast2</logdir>
        <webroot>/usr/share/icecast2/web</webroot>
        <adminroot>/usr/share/icecast2/web</adminroot>
    </paths>
    
    <logging>
        <accesslog>access.log</accesslog>
        <errorlog>error.log</errorlog>
        <loglevel>2</loglevel>
    </logging>
</icecast>
"""
            # Write config and start icecast
            with open('/tmp/icecast.xml', 'w') as f:
                f.write(icecast_config)
            
            # Start icecast in background
            subprocess.Popen(['icecast2', '-c', '/tmp/icecast.xml', '-b'])
            print("🎧 Icecast server started")
            
        except Exception as e:
            print(f"Icecast start error: {e}")
    
    def stop_radio_stream(self):
        """Stop radio stream"""
        global current_stream_process, player_status, current_track
        
        if current_stream_process:
            current_stream_process.terminate()
            current_stream_process = None
        
        current_track = None
        player_status = "stopped"

streamer = AzuraCastStreamer()

@app.get("/")
async def root():
    return {
        "message": "AzuraCast Radio API",
        "status": "online",
        "radio_url": f"http://{os.getenv('RAILWAY_STATIC_URL', 'localhost:8000')}:8001{ICECAST_CONFIG['mount']}"
    }

@app.get("/api/search")
async def search_music(q: str):
    """Search for music"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    results = streamer.search_youtube(q)
    return {"query": q, "results": results}

@app.post("/api/play")
async def play_music(video_url: str, background_tasks: BackgroundTasks):
    """Start radio stream"""
    success = streamer.start_radio_stream(video_url)
    
    if success:
        radio_url = f"http://{os.getenv('RAILWAY_STATIC_URL', 'localhost:8000')}:8001{ICECAST_CONFIG['mount']}"
        return {
            "status": "playing",
            "track": current_track,
            "radio_url": radio_url,
            "message": "Radio stream started! Add the radio URL to Highrise."
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to start radio stream")

@app.post("/api/stop")
async def stop_music():
    """Stop radio stream"""
    streamer.stop_radio_stream()
    return {"status": "stopped", "message": "Radio stopped"}

@app.get("/api/status")
async def get_player_status():
    """Get player status"""
    return {
        "status": player_status,
        "current_track": current_track,
        "stream_active": current_stream_process is not None
    }

@app.get("/api/radio/url")
async def get_radio_url():
    """Get radio stream URL for Highrise"""
    radio_url = f"http://{os.getenv('RAILWAY_STATIC_URL', 'localhost:8000')}:8001{ICECAST_CONFIG['mount']}"
    
    return {
        "radio_url": radio_url,
        "status": player_status,
        "current_track": current_track['title'] if current_track else None,
        "instructions": "Add this URL to Highrise room music settings!"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "radio_streaming": current_stream_process is not None,
        "icecast_port": 8001
    }