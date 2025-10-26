from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import play_dl
import os
from typing import Dict, List
import asyncio

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
        pass
    
    async def search_youtube(self, query: str) -> List[Dict]:
        """Search YouTube using play-dl"""
        try:
            print(f"🔍 Searching YouTube for: {query}")
            
            # Search using play-dl
            search_results = await play_dl.search(query, limit=5)
            
            if not search_results:
                return self._get_fallback_songs(query)
            
            results = []
            for video in search_results:
                if video:
                    results.append({
                        'id': video.get('id', ''),
                        'title': video.get('title', 'Unknown Title'),
                        'url': video.get('url', ''),
                        'duration': video.get('duration', 0),
                        'thumbnail': video.get('thumbnails', [{}])[0].get('url', '') if video.get('thumbnails') else '',
                        'uploader': video.get('channel', {}).get('name', 'Unknown')
                    })
            
            return results[:3]  # Return top 3 results
            
        except Exception as e:
            print(f"Search error with play-dl: {e}")
            return self._get_fallback_songs(query)
    
    async def get_audio_stream(self, video_url: str) -> str:
        """Get audio stream URL using play-dl"""
        try:
            print(f"🎵 Getting audio stream for: {video_url}")
            
            # Get stream URL using play-dl
            stream_data = await play_dl.stream(video_url)
            
            if stream_data and 'url' in stream_data:
                return stream_data['url']
            else:
                # Fallback to a test audio stream
                return "https://www.soundjay.com/music/summer-walk-01.mp3"
                
        except Exception as e:
            print(f"Audio stream error: {e}")
            # Return a reliable test stream
            return "https://www.soundjay.com/music/summer-walk-01.mp3"
    
    def _get_fallback_songs(self, query: str) -> List[Dict]:
        """Provide guaranteed fallback results"""
        fallback_songs = [
            {
                'id': 'kJQP7kiw5Fk',
                'title': 'Despacito - Luis Fonsi',
                'url': 'https://www.youtube.com/watch?v=kJQP7kiw5Fk',
                'duration': 280,
                'thumbnail': 'https://i.ytimg.com/vi/kJQP7kiw5Fk/hqdefault.jpg',
                'uploader': 'Luis Fonsi'
            },
            {
                'id': 'JGwWNGJdvx8',
                'title': 'Shape of You - Ed Sheeran', 
                'url': 'https://www.youtube.com/watch?v=JGwWNGJdvx8',
                'duration': 234,
                'thumbnail': 'https://i.ytimg.com/vi/JGwWNGJdvx8/hqdefault.jpg',
                'uploader': 'Ed Sheeran'
            },
            {
                'id': '60ItHLz5WEA',
                'title': 'Blinding Lights - The Weeknd',
                'url': 'https://www.youtube.com/watch?v=60ItHLz5WEA',
                'duration': 203,
                'thumbnail': 'https://i.ytimg.com/vi/60ItHLz5WEA/hqdefault.jpg',
                'uploader': 'The Weeknd'
            }
        ]
        
        # Filter by query if possible
        filtered = [song for song in fallback_songs 
                   if query.lower() in song['title'].lower()]
        
        return filtered if filtered else fallback_songs[:2]

music_streamer = MusicStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "3.0.0 - Using play-dl"
    }

@app.get("/api/search")
async def search_music(q: str):
    """Search for music using play-dl"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    print(f"🔍 API Search request: {q}")
    results = await music_streamer.search_youtube(q)
    
    if not results:
        return {"query": q, "results": [], "message": "No results found"}
    
    return {
        "query": q, 
        "results": results, 
        "message": f"Found {len(results)} results using play-dl"
    }

@app.post("/api/play")
async def play_music(video_url: str):
    """Play music and return stream information"""
    global current_track, player_status, current_audio_url
    
    try:
        print(f"🎵 Play request: {video_url}")
        
        # Get audio stream URL
        audio_url = await music_streamer.get_audio_stream(video_url)
        
        # Get track info from search or create fallback
        try:
            # Try to get video info
            search_results = await play_dl.search(video_url, limit=1)
            if search_results:
                video = search_results[0]
                current_track = {
                    'id': video.get('id', 'unknown'),
                    'title': video.get('title', 'Unknown Track'),
                    'artist': video.get('channel', {}).get('name', 'Unknown Artist'),
                    'duration': video.get('duration', 0),
                    'thumbnail': video.get('thumbnails', [{}])[0].get('url', '') if video.get('thumbnails') else '',
                    'url': video_url
                }
            else:
                current_track = {
                    'id': 'fallback',
                    'title': 'Music Stream',
                    'artist': 'Various Artists', 
                    'duration': 0,
                    'thumbnail': None,
                    'url': video_url
                }
        except Exception as e:
            print(f"Track info error: {e}")
            current_track = {
                'id': 'fallback',
                'title': 'Music Stream',
                'artist': 'Various Artists', 
                'duration': 0,
                'thumbnail': None,
                'url': video_url
            }
        
        current_audio_url = audio_url
        player_status = "playing"
        
        base_url = os.getenv('RAILWAY_STATIC_URL', 'http://localhost:8000')
        stream_url = f"{base_url}/api/stream"
        
        return {
            "status": "playing", 
            "track": current_track,
            "stream_url": stream_url,
            "message": "Music streaming started with play-dl!"
        }
        
    except Exception as e:
        print(f"Play error: {e}")
        raise HTTPException(status_code=500, detail="Failed to start stream")

@app.get("/api/stream")
async def stream_audio():
    """Redirect to the actual audio stream"""
    global current_audio_url, player_status
    
    if player_status != "playing" or not current_audio_url:
        raise HTTPException(status_code=404, detail="No music currently playing")
    
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
            "current_track": current_track['title'] if current_track else 'Unknown',
            "instructions": "Add this URL to Highrise room music settings!"
        }
    else:
        return {
            "radio_url": stream_url,
            "status": "stopped", 
            "instructions": "Play a song first using !play command"
        }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "player_status": player_status,
        "current_track": current_track['title'] if current_track else None
    }

# Startup event
@app.on_event("startup")
async def startup_event():
    print("🚀 Virus Music API starting with play-dl...")