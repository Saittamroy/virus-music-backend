from fastapi import FastAPI, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import aiohttp
import os
from typing import Dict, List
import json
import requests

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
    
    async def search_music(self, query: str) -> List[Dict]:
        """Search music using Invidious API (YouTube alternative)"""
        try:
            print(f"🔍 Searching for: {query}")
            
            # Use Invidious API instead of YouTube directly
            invidious_instances = [
                "https://inv.tux.pizza",
                "https://invidious.snopyta.org", 
                "https://yewtu.be"
            ]
            
            for instance in invidious_instances:
                try:
                    search_url = f"{instance}/api/v1/search?q={query}&type=video"
                    response = requests.get(search_url, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        results = []
                        
                        for video in data[:5]:  # Top 5 results
                            results.append({
                                'id': video['videoId'],
                                'title': video['title'],
                                'url': f"https://www.youtube.com/watch?v={video['videoId']}",
                                'duration': video.get('lengthSeconds', 0),
                                'thumbnail': video.get('videoThumbnails', [{}])[0].get('url', ''),
                                'artist': video.get('author', 'Unknown Artist'),
                                'source': 'invidious'
                            })
                        
                        if results:
                            print(f"✅ Found {len(results)} results via {instance}")
                            return results
                            
                except Exception as e:
                    print(f"❌ Invidious instance {instance} failed: {e}")
                    continue
            
            # Fallback to curated music if all Invidious instances fail
            return self._get_curated_music(query)
            
        except Exception as e:
            print(f"❌ Search error: {e}")
            return self._get_curated_music(query)
    
    def _get_curated_music(self, query: str) -> List[Dict]:
        """Curated list of free music that always works"""
        curated_music = [
            {
                'id': 'bensound1',
                'title': 'Acoustic Breeze',
                'artist': 'Bensound',
                'duration': 157,
                'thumbnail': '',
                'url': 'https://www.bensound.com/bensound-music/bensound-acousticbreeze.mp3',
                'source': 'bensound'
            },
            {
                'id': 'bensound2',
                'title': 'Better Days',
                'artist': 'Bensound',
                'duration': 188,
                'thumbnail': '',
                'url': 'https://www.bensound.com/bensound-music/bensound-betterdays.mp3',
                'source': 'bensound'
            },
            {
                'id': 'bensound3',
                'title': 'Happy Rock',
                'artist': 'Bensound',
                'duration': 167,
                'thumbnail': '',
                'url': 'https://www.bensound.com/bensound-music/bensound-happyrock.mp3',
                'source': 'bensound'
            },
            {
                'id': 'soundjay1',
                'title': 'Summer Walk',
                'artist': 'Background Music',
                'duration': 180,
                'thumbnail': '',
                'url': 'https://www.soundjay.com/music/summer-walk-01.mp3',
                'source': 'soundjay'
            }
        ]
        
        # Filter by query if provided
        if query and query.strip():
            filtered = [track for track in curated_music 
                       if query.lower() in track['title'].lower() or 
                          query.lower() in track['artist'].lower()]
            return filtered if filtered else curated_music
        
        return curated_music
    
    async def get_track_by_url(self, video_url: str) -> Dict:
        """Get track information by URL - FIXED VERSION"""
        try:
            print(f"🎵 Looking up track by URL: {video_url}")
            
            # For curated music URLs, return the track directly
            curated_tracks = self._get_curated_music("")
            for track in curated_tracks:
                if track['url'] == video_url:
                    print(f"✅ Found curated track: {track['title']}")
                    return track
            
            # For YouTube URLs, extract video ID and search
            if 'youtube.com' in video_url or 'youtu.be' in video_url:
                video_id = None
                if 'youtube.com/watch?v=' in video_url:
                    video_id = video_url.split('youtube.com/watch?v=')[1].split('&')[0]
                elif 'youtu.be/' in video_url:
                    video_id = video_url.split('youtu.be/')[1].split('?')[0]
                
                if video_id:
                    # Search for this specific video
                    invidious_instances = [
                        "https://inv.tux.pizza",
                        "https://invidious.snopyta.org", 
                        "https://yewtu.be"
                    ]
                    
                    for instance in invidious_instances:
                        try:
                            video_url = f"{instance}/api/v1/videos/{video_id}"
                            response = requests.get(video_url, timeout=10)
                            
                            if response.status_code == 200:
                                video_data = response.json()
                                track_info = {
                                    'id': video_data['videoId'],
                                    'title': video_data['title'],
                                    'url': f"https://www.youtube.com/watch?v={video_data['videoId']}",
                                    'duration': video_data.get('lengthSeconds', 0),
                                    'thumbnail': video_data.get('videoThumbnails', [{}])[3].get('url', ''),
                                    'artist': video_data.get('author', 'Unknown Artist'),
                                    'source': 'invidious'
                                }
                                print(f"✅ Found YouTube track: {track_info['title']}")
                                return track_info
                                
                        except Exception as e:
                            print(f"❌ Invidious instance {instance} failed: {e}")
                            continue
            
            # Final fallback - return first curated track
            fallback_track = curated_tracks[0]
            print(f"⚠️ Using fallback track: {fallback_track['title']}")
            return fallback_track
            
        except Exception as e:
            print(f"❌ Track lookup error: {e}")
            curated_tracks = self._get_curated_music("")
            return curated_tracks[0]

music_streamer = MusicStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "6.1.0 - Fixed Track Selection"
    }

@app.get("/api/search")
async def search_music(q: str):
    """Search for music - always returns results"""
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter required")
    
    print(f"🎵 API Search: {q}")
    results = await music_streamer.search_music(q)
    
    return {
        "query": q, 
        "results": results, 
        "message": f"Found {len(results)} tracks",
        "source": "invidious+curated"
    }

@app.post("/api/play")
async def play_music(video_url: str = Form(...)):
    """Play music - FIXED VERSION that properly selects requested track"""
    global current_track, player_status, current_audio_url
    
    try:
        print(f"🎵 Play request received: {video_url}")
        
        # Get the actual track info for the requested URL
        track_info = await music_streamer.get_track_by_url(video_url)
        
        print(f"🎵 Selected track: {track_info['title']}")
        
        # Set current track
        current_track = {
            'id': track_info['id'],
            'title': track_info['title'],
            'artist': track_info.get('artist', 'Unknown Artist'),
            'duration': track_info['duration'],
            'thumbnail': track_info.get('thumbnail', ''),
            'url': track_info['url'],  # Use the actual URL
            'source': track_info.get('source', 'direct')
        }
        
        current_audio_url = track_info['url']  # Use the actual audio URL
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
        raise HTTPException(status_code=500, detail="Failed to start stream")

@app.get("/api/stream")
async def stream_audio():
    """Redirect to audio stream - always works"""
    global current_audio_url, player_status
    
    if player_status == "playing" and current_audio_url:
        print(f"🎵 Streaming: {current_audio_url}")
        return RedirectResponse(url=current_audio_url)
    else:
        # Always return a working audio stream
        default_url = "https://www.bensound.com/bensound-music/bensound-acousticbreeze.mp3"
        print(f"🎵 Streaming default: {default_url}")
        return RedirectResponse(url=default_url)

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
        "sources": "invidious, bensound, soundjay"
    }