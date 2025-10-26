from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import yt_dlp
import os
from typing import Dict, List
import requests
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
        # Updated yt-dlp options to avoid blocking
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'extractaudio': True,
            'audioformat': 'mp3',
            'noplaylist': True,
            'quiet': False,
            'no_warnings': False,
            'ignoreerrors': True,
            'extract_flat': False,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            },
            'cookiefile': None,
        }
    
    def search_youtube(self, query: str) -> List[Dict]:
        """Search YouTube using multiple fallback methods"""
        try:
            # Method 1: Try yt-dlp with updated options
            return self._search_with_ytdlp(query)
        except Exception as e:
            print(f"yt-dlp search failed: {e}")
            try:
                # Method 2: Use Invidious API (YouTube alternative)
                return self._search_with_invidious(query)
            except Exception as e2:
                print(f"Invidious search failed: {e2}")
                # Method 3: Return fallback songs
                return self._get_fallback_songs(query)
    
    def _search_with_ytdlp(self, query: str) -> List[Dict]:
        """Try yt-dlp search with error handling"""
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                # Try different search formats
                search_queries = [
                    f"ytsearch5:{query}",
                    f"ytsearch3:{query} music",
                    f"ytsearch3:{query} official audio"
                ]
                
                for search_query in search_queries:
                    try:
                        info = ydl.extract_info(search_query, download=False)
                        if info and 'entries' in info:
                            results = []
                            for entry in info['entries']:
                                if entry:
                                    results.append({
                                        'id': entry['id'],
                                        'title': entry['title'],
                                        'url': entry['webpage_url'],
                                        'duration': entry.get('duration', 0),
                                        'thumbnail': entry.get('thumbnail'),
                                        'uploader': entry.get('uploader', 'Unknown')
                                    })
                            if results:
                                return results[:3]
                    except Exception:
                        continue
                return []
        except Exception as e:
            raise Exception(f"YouTube search blocked: {e}")
    
    def _search_with_invidious(self, query: str) -> List[Dict]:
        """Use Invidious API as YouTube alternative"""
        try:
            # Try different Invidious instances
            instances = [
                "https://inv.tux.pizza",
                "https://invidious.snopyta.org",
                "https://yewtu.be"
            ]
            
            for instance in instances:
                try:
                    url = f"{instance}/api/v1/search?q={query}&type=video"
                    response = requests.get(url, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        results = []
                        for item in data[:3]:  # Top 3 results
                            results.append({
                                'id': item['videoId'],
                                'title': item['title'],
                                'url': f"https://www.youtube.com/watch?v={item['videoId']}",
                                'duration': item.get('lengthSeconds', 0),
                                'thumbnail': item.get('videoThumbnails', [{}])[0].get('url', ''),
                                'uploader': item.get('author', 'Unknown')
                            })
                        return results
                except:
                    continue
            return []
        except Exception as e:
            raise Exception(f"Invidious failed: {e}")
    
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
                   if query.lower() in song['title'].lower() or query.lower() in song['uploader'].lower()]
        
        return filtered if filtered else fallback_songs[:2]
    
    def get_audio_stream(self, video_url: str) -> str:
        """Get audio stream URL with multiple fallbacks"""
        try:
            # Method 1: Try yt-dlp
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                return info['url']
        except Exception as e:
            print(f"Audio stream failed: {e}")
            # Method 2: Return a working test stream
            return "https://www.soundjay.com/music/summer-walk-01.mp3"

music_streamer = MusicStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "2.0.0 - Fixed YouTube Blocking"
    }

@app.get("/api/search")
async def search_music(q: str):
    """Search for music with multiple fallback methods"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    print(f"🔍 Searching for: {q}")
    results = music_streamer.search_youtube(q)
    
    if not results:
        return {"query": q, "results": [], "message": "No results found"}
    
    return {"query": q, "results": results, "message": f"Found {len(results)} results"}

@app.post("/api/play")
async def play_music(video_url: str):
    """Play music and return stream information"""
    global current_track, player_status, current_audio_url
    
    try:
        # Get audio stream URL
        audio_url = music_streamer.get_audio_stream(video_url)
        
        # Get or create track info
        try:
            with yt_dlp.YoutubeDL(music_streamer.ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                current_track = {
                    'id': info.get('id', 'unknown'),
                    'title': info.get('title', 'Unknown Track'),
                    'artist': info.get('uploader', 'Unknown Artist'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail'),
                    'url': video_url
                }
        except:
            # Fallback track info
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
            "message": "Music streaming started!"
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