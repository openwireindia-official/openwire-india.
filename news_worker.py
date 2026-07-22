import os
import sys
import glob
import json
import random
import asyncio
import time
import shutil
import subprocess
import requests
import edge_tts
from mutagen.mp3 import MP3
import g4f
import yt_dlp
from supabase import create_client, Client

# --- YOUTUBE API IMPORTS ---
import pickle
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- ENVIRONMENT & CONFIGURATION ---
BASE_DIR = os.getcwd()
CLIPS_DIR = os.path.join(BASE_DIR, "clips")
VOICEOVER_FILE = os.path.join(BASE_DIR, "news_voiceover.mp3")
SUBS_FILE = os.path.join(BASE_DIR, "news_subs.ass")
FINAL_OUTPUT = os.path.join(BASE_DIR, "FINAL_NEWS_VIDEO.mp4")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

USA_VOICE = "en-US-AvaMultilingualNeural"
MAX_RETRIES = 3

os.makedirs(CLIPS_DIR, exist_ok=True)

# --- SUPABASE DATA FETCH ---
def get_target_request():
    target_id = os.getenv("TARGET_REQUEST_ID")
    if target_id:
        res = supabase.table("news_requests").select("*").eq("id", target_id).execute()
        if res.data:
            return res.data[0]
    
    # Fallback: Pick oldest pending item
    res = supabase.table("news_requests").select("*").eq("status", "pending").order("created_at").limit(1).execute()
    return res.data[0] if res.data else None

def update_request_status(request_id, status_str):
    supabase.table("news_requests").update({"status": status_str}).eq("id", request_id).execute()

# --- YOUTUBE AUTHENTICATION ---
def get_youtube_service():
    pickle_path = os.path.join(BASE_DIR, "token.pickle")
    if not os.path.exists(pickle_path):
        print("❌ Token pickle file missing!")
        return None
        
    with open(pickle_path, "rb") as token:
        creds = pickle.load(token)
        
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(pickle_path, "wb") as token:
            pickle.dump(creds, token)
            
    return build("youtube", "v3", credentials=creds)

def upload_short_to_youtube(video_path, title, description, tags):
    youtube = get_youtube_service()
    if not youtube:
        return False
        
    clean_tags = [t.replace("#", "") for t in tags]
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "categoryId": "25", # News & Politics
            "tags": clean_tags
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }
    
    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(video_path, chunksize=1024*1024, resumable=True)
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
        return response.get("id")
    except Exception as e:
        print(f"❌ YouTube API Error: {e}")
        return None

# --- ASSET SCRAPING & RENDERING ENGINE ---
def format_ass_time(seconds):
    centiseconds = int((seconds - int(seconds)) * 100)
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}.{centiseconds:02d}"

async def generate_voiceover(text, output_path):
    communicate = edge_tts.Communicate(text, USA_VOICE, rate="+2%", pitch="-1Hz")
    await communicate.save(output_path)

def download_video_assets(request_data, search_query):
    if os.path.exists(CLIPS_DIR):
        shutil.rmtree(CLIPS_DIR)
    os.makedirs(CLIPS_DIR, exist_ok=True)

    # 1. User Uploaded Evidence Video
    if request_data['type'] == 'raw_video' and request_data.get('video_url'):
        print("📥 Downloading submitted video evidence from storage...")
        res = requests.get(request_data['video_url'], stream=True)
        out_path = os.path.join(CLIPS_DIR, "evidence.mp4")
        with open(out_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=1024*1024):
                if chunk: f.write(chunk)
        return True

    # 2. Text Topic Request: Scrape matching news clips
    print(f"🔍 Searching YouTube clips for query: '{search_query}'...")
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(CLIPS_DIR, '%(id)s.%(ext)s'),
        'quiet': True,
        'match_filter': yt_dlp.utils.match_filter_func("duration < 600") 
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"ytsearch2:{search_query} news ground reality 1080p"])
        return True
    except Exception as e:
        print(f"❌ Scraping failed: {e}")
        return False

def main():
    item = get_target_request()
    if not item:
        print("⚡ No pending news requests in Supabase.")
        return

    req_id = item['id']
    print(f"🚀 Processing Request ID: {req_id}")
    update_request_status(req_id, "processing")

    # 1. AI Script Generation
    master_prompt = f"""
    You are an investigative journalist for OpenWire India.
    Write a 25-second urgent news short script based on this report: "{item['content']}".

    RULES:
    - Tone: Serious, factual, fast-paced.
    - End with: "This is OpenWire India. Leak the bubble."
    - Return STRICTLY JSON with no markdown:
    {{
      "title": "3 TO 5 WORD HEADLINE",
      "search_query": "3 word generic search term for visual background",
      "tags": ["#OpenWire", "#IndiaNews", "#Unfiltered"],
      "script_text": "Narration text copy here..."
    }}
    """

    script_data = None
    for _ in range(MAX_RETRIES):
        try:
            res = g4f.ChatCompletion.create(
                model=g4f.models.gpt_4,
                messages=[{"role": "user", "content": master_prompt}]
            )
            clean_json = res.replace("```json", "").replace("```", "").strip()
            script_data = json.loads(clean_json)
            break
        except Exception:
            time.sleep(3)

    if not script_data:
        update_request_status(req_id, "failed")
        return

    # 2. Download Media Assets
    if not download_video_assets(item, script_data['search_query']):
        update_request_status(req_id, "failed")
        return

    # 3. Audio & Subtitles
    asyncio.run(generate_voiceover(script_data['script_text'], VOICEOVER_FILE))
    audio_dur = MP3(VOICEOVER_FILE).info.length

    words = script_data['script_text'].split()
    chunks = [" ".join(words[i:i+3]) for i in range(0, len(words), 3)]
    dur_per_chunk = audio_dur / max(1, len(chunks))

    ass_header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,85,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,5,30,30,900,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    curr_t = 0.0
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        f.write(ass_header)
        for c in chunks:
            f.write(f"Dialogue: 0,{format_ass_time(curr_t)},{format_ass_time(curr_t + dur_per_chunk)},Default,,0,0,0,,{c.upper()}\n")
            curr_t += dur_per_chunk

    # 4. FFmpeg Build Process
    clips = glob.glob(os.path.join(CLIPS_DIR, "*.mp4"))
    if not clips:
        update_request_status(req_id, "failed")
        return

    selected_clip = clips[0]
    cmd = f"ffmpeg -y -stream_loop -1 -i '{selected_clip}' -i '{VOICEOVER_FILE}' -filter_complex \"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,ass={SUBS_FILE}[v];[1:a]volume=1.5[a]\" -map '[v]' -map '[a]' -t {audio_dur} -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac '{FINAL_OUTPUT}' -loglevel error"
    
    if os.system(cmd) != 0:
        update_request_status(req_id, "failed")
        return

    # 5. YouTube Deployment
    desc = f"{script_data['script_text']}\n\n{' '.join(script_data['tags'])}"
    yt_id = upload_short_to_youtube(FINAL_OUTPUT, script_data['title'], desc, script_data['tags'])

    if yt_id:
        print(f"🎉 Successfully deployed to YouTube! ID: {yt_id}")
        update_request_status(req_id, "published")
    else:
        update_request_status(req_id, "failed")

    # Cleanup local run files
    if os.path.exists(VOICEOVER_FILE): os.remove(VOICEOVER_FILE)
    if os.path.exists(SUBS_FILE): os.remove(SUBS_FILE)
    if os.path.exists(FINAL_OUTPUT): os.remove(FINAL_OUTPUT)

if __name__ == "__main__":
    main()
      
