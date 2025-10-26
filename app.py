from fastapi import FastAPI, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import aiohttp
import os
from typing import Dict, List
import json

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
        self.deezer_base = "https://api.deezer.com"
    
    async def search_music(self, query: str) -> List[Dict]:
        """Search music using Deezer API"""
        try:
            print(f"🔍 Searching Deezer for: {query}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.deezer_base}/search?q={query}&limit=5") as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        results = []
                        for track in data.get('data', [])[:5]:
                            # Only include tracks with previews
                            if track.get('preview'):
                                results.append({
                                    'id': str(track['id']),
                                    'title': track['title'],
                                    'artist': track['artist']['name'],
                                    'duration': track['duration'],
                                    'thumbnail': track['album']['cover_medium'],
                                    'preview_url': track.get('preview', ''),
                                    'source': 'deezer'
                                })
                        
                        if results:
                            return results
                    
                    # Fallback to free music
                    return self._get_curated_music(query)
                    
        except Exception as e:
            print(f"Deezer search error: {e}")
            return self._get_curated_music(query)
    
    def _get_curated_music(self, query: str) -> List[Dict]:
        """Curated list of free-to-use music that actually plays"""
        free_music = [
            {
                'id': '1',
                'title': 'Summer Walk',
                'artist': 'Background Music',
                'duration': 180,
                'thumbnail': '',
                'preview_url': 'https://www.soundjay.com/music/summer-walk-01.mp3',
                'source': 'soundjay'
            },
            {
                'id': '2', 
                'title': 'Acoustic Breeze',
                'artist': 'Bensound',
                'duration': 157,
                'thumbnail': '',
                'preview_url': 'https://www.bensound.com/bensound-music/bensound-acousticbreeze.mp3',
                'source': 'bensound'
            },
            {
                'id': '3',
                'title': 'Better Days',
                'artist': 'Bensound', 
                'duration': 188,
                'thumbnail': '',
                'preview_url': 'https://www.bensound.com/bensound-music/bensound-betterdays.mp3',
                'source': 'bensound'
            },
            {
                'id': '4',
                'title': 'Happy Rock',
                'artist': 'Bensound',
                'duration': 167,
                'thumbnail': '',
                'preview_url': 'https://www.bensound.com/bensound-music/bensound-happyrock.mp3',
                'source': 'bensound'
            }
        ]
        
        # Filter by query
        if query:
            filtered = [track for track in free_music 
                       if query.lower() in track['title'].lower() or query.lower() in track['artist'].lower()]
            return filtered if filtered else free_music[:2]
        
        return free_music[:3]
    
    async def get_audio_stream(self, track_data: Dict) -> str:
        """Get audio stream URL from track data"""
        try:
            # Use the preview URL directly
            preview_url = track_data.get('preview_url')
            if preview_url:
                print(f"🎵 Using preview URL: {preview_url}")
                return preview_url
            
            # Final fallback - guaranteed working audio
            return "https://www.soundjay.com/music/summer-walk-01.mp3"
            
        except Exception as e:
            print(f"Audio stream error: {e}")
            return "https://www.soundjay.com/music/summer-walk-01.mp3"

music_streamer = MusicStreamer()

@app.post("/api/play")
async def play_music(track_data: str = Form(...)):
    """Play music from free APIs - FIXED FORM DATA"""
    global current_track, player_status, current_audio_url
    
    try:
        print(f"🎵 Received play request with track data")
        
        # Parse track data
        track = json.loads(track_data)
        print(f"🎵 Playing: {track.get('title', 'Unknown')}")
        
        # Get audio stream
        audio_url = await music_streamer.get_audio_stream(track)
        print(f"🎵 Audio URL: {audio_url}")
        
        current_track = {
            'id': track.get('id', ''),
            'title': track.get('title', 'Unknown Track'),
            'artist': track.get('artist', 'Unknown Artist'),
            'duration': track.get('duration', 0),
            'thumbnail': track.get('thumbnail', ''),
            'url': audio_url,
            'source': track.get('source', 'free')
        }
        
        current_audio_url = audio_url
        player_status = "playing"
        
        base_url = os.getenv('RAILWAY_STATIC_URL', 'http://localhost:8000')
        stream_url = f"{base_url}/api/stream"
        
        return {
            "status": "playing", 
            "track": current_track,
            "stream_url": stream_url,
            "message": f"🎵 Now playing: {current_track['title']} - {current_track['artist']}"
        }
        
    except Exception as e:
        print(f"Play error: {e}")
        raise HTTPException(status_code=500, detail="Failed to start stream")

# Keep all other endpoints the same as before...