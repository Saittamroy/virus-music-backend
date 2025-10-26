from fastapi import FastAPI, HTTPException
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
                    
                    # Fallback to Jamendo API
                    return await self.search_jamendo(query)
                    
        except Exception as e:
            print(f"Deezer search error: {e}")
            return await self.search_jamendo(query)
    
    async def search_jamendo(self, query: str) -> List[Dict]:
        """Search free music using Jamendo API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.jamendo.com/v3.0/tracks/?client_id=YOUR_CLIENT_ID&format=json&limit=5&search={query}") as response:
                    if response.status == 200:
                        data = await response.json()
                        results = []
                        for track in data.get('results', [])[:3]:
                            results.append({
                                'id': track['id'],
                                'title': track['name'],
                                'artist': track['artist_name'],
                                'duration': track['duration'],
                                'thumbnail': track['album_image'],
                                'preview_url': track['audio'],
                                'source': 'jamendo'
                            })
                        return results
        except:
            pass
        
        # Final fallback - curated free music
        return self._get_curated_music(query)
    
    def _get_curated_music(self, query: str) -> List[Dict]:
        """Curated list of free-to-use music"""
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
                'title': 'Jazz Piano',
                'artist': 'Background Music',
                'duration': 120,
                'thumbnail': '',
                'preview_url': 'https://ccrma.stanford.edu/~jos/mp3/gtr-nylon22.mp3',
                'source': 'ccrma'
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
            if track_data.get('preview_url'):
                return track_data['preview_url']
            
            # Fallback to Deezer preview
            if track_data.get('source') == 'deezer' and track_data.get('id'):
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{self.deezer_base}/track/{track_data['id']}") as response:
                        if response.status == 200:
                            data = await response.json()
                            return data.get('preview', '')
            
            # Final fallback
            return "https://www.soundjay.com/music/summer-walk-01.mp3"
            
        except Exception as e:
            print(f"Audio stream error: {e}")
            return "https://www.soundjay.com/music/summer-walk-01.mp3"

music_streamer = MusicStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "5.0.0 - Deezer & Free Music APIs"
    }

@app.get("/api/search")
async def search_music(q: str):
    """Search music using multiple free APIs"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    print(f"🎵 API Search: {q}")
    results = await music_streamer.search_music(q)
    
    return {
        "query": q, 
        "results": results, 
        "message": f"Found {len(results)} tracks",
        "source": "deezer+free_music"
    }

@app.post("/api/play")
async def play_music(track_data: str):
    """Play music from free APIs"""
    global current_track, player_status, current_audio_url
    
    try:
        # Parse track data (sent as JSON string)
        track = json.loads(track_data)
        print(f"🎵 Playing: {track.get('title', 'Unknown')}")
        
        # Get audio stream
        audio_url = await music_streamer.get_audio_stream(track)
        
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

@app.get("/api/stream")
async def stream_audio():
    """Redirect to audio stream"""
    global current_audio_url, player_status
    
    if player_status != "playing" or not current_audio_url:
        # Return a default free music stream
        return RedirectResponse(url="https://www.soundjay.com/music/summer-walk-01.mp3")
    
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
    
    return {
        "radio_url": stream_url,
        "status": player_status,
        "current_track": current_track['title'] if current_track else 'Ready to play',
        "instructions": "Add this URL to Highrise room music settings!"
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "player_status": player_status,
        "sources": "deezer, jamendo, free_music"
    }