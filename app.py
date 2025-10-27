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
                                'uploader': video.get('author', 'Unknown Artist'),
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
        if query:
            filtered = [track for track in curated_music 
                       if query.lower() in track['title'].lower() or query.lower() in track['artist'].lower()]
            return filtered if filtered else curated_music[:2]
        
        return curated_music[:3]
    
    async def get_audio_stream(self, track_data: Dict) -> str:
        """Get audio stream URL - uses direct MP3 URLs"""
        try:
            # For Bensound and SoundJay, use the URL directly
            if track_data.get('source') in ['bensound', 'soundjay']:
                return track_data['url']
            
            # For YouTube videos, try to get stream (but we'll mainly use curated music)
            video_url = track_data.get('url', '')
            if 'youtube.com' in video_url or 'youtu.be' in video_url:
                # Fallback to curated music instead of trying YouTube
                curated = self._get_curated_music('')
                return curated[0]['url']
            
            # Final fallback
            return "https://www.bensound.com/bensound-music/bensound-acousticbreeze.mp3"
            
        except Exception as e:
            print(f"❌ Audio stream error: {e}")
            return "https://www.bensound.com/bensound-music/bensound-acousticbreeze.mp3"

music_streamer = MusicStreamer()

@app.get("/")
async def root():
    return {
        "message": "Virus Music Radio API", 
        "status": "online",
        "version": "6.0.0 - Invidious + Curated Music"
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
    """Play music - FIXED FORM PARAMETER"""
    global current_track, player_status, current_audio_url
    
    try:
        print(f"🎵 Play request received: {video_url}")
        
        # First, search to get full track info
        search_results = await music_streamer.search_music("")
        track_info = None
        
        # Find the track in search results
        for track in search_results:
            if track['url'] == video_url:
                track_info = track
                break
        
        if not track_info:
            # Use first curated track as fallback
            track_info = music_streamer._get_curated_music("")[0]
        
        # Get audio stream URL
        audio_url = await music_streamer.get_audio_stream(track_info)
        print(f"🎵 Audio URL: {audio_url}")
        
        # Set current track
        current_track = {
            'id': track_info['id'],
            'title': track_info['title'],
            'artist': track_info.get('artist', track_info.get('uploader', 'Unknown Artist')),
            'duration': track_info['duration'],
            'thumbnail': track_info.get('thumbnail', ''),
            'url': audio_url,
            'source': track_info.get('source', 'direct')
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
            "message": f"🎵 Now playing: {current_track['title']}"
        }
        
    except Exception as e:
        print(f"❌ Play error: {e}")
        raise HTTPException(status_code=500, detail="Failed to start stream")

@app.get("/api/stream")
async def stream_audio():
    """Redirect to audio stream - always works"""
    global current_audio_url, player_status
    
    if player_status == "playing" and current_audio_url:
        return RedirectResponse(url=current_audio_url)
    else:
        # Always return a working audio stream
        return RedirectResponse(url="https://www.bensound.com/bensound-music/bensound-acousticbreeze.mp3")

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