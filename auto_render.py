import os
import glob
import json
import random
import asyncio
import time
import requests
from datetime import datetime, timezone
from supabase import create_client, Client
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import edge_tts
from mutagen.mp3 import MP3
from google import genai

# ==========================================
# 1. DIRECTORY & PATH CONFIGURATION
# ==========================================
BASE_DIR = "/storage/emulated/0/OpenWire/"
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloaded_clips/")
MUSIC_DIR = os.path.join(BASE_DIR, "music/")
VOICEOVER_FILE = os.path.join(BASE_DIR, "voiceover.mp3")
SUBS_FILE = os.path.join(BASE_DIR, "subs.ass")
FINAL_OUTPUT = os.path.join(BASE_DIR, "FINAL_OPENWIRE_REEL.mp4")
FONT_FILE = os.path.join(BASE_DIR, "Arial Bold.ttf")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)

# ==========================================
# 2. CREDENTIALS & API CONFIGURATION
# ==========================================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "X9z62cFEWZnEYQKqiRsqWvVToRNjFRx0ASNg02ZyHGtbOEjrcBaxHsW3")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyAgtX3tzvDaLGoC2X_bsb_HcBI5kLXUWYY")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

DAILY_UPLOAD_LIMIT = 20
VOICE = "en-US-BrianNeural"

# ==========================================
# 3. HELPER FUNCTIONS & QUOTA ENGINE
# ==========================================
def format_ass_time(seconds):
    centiseconds = int((seconds - int(seconds)) * 100)
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}.{centiseconds:02d}"

async def generate_voiceover(text, output_path):
    communicate = edge_tts.Communicate(text, VOICE, rate="-2%", pitch="-1Hz")
    await communicate.save(output_path)
    return True

def get_today_published_count():
    """Counts videos published today to respect API daily limits."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    res = supabase.table("news_requests").select("id", count="exact").eq("status", "published").gte("created_at", today_start).execute()
    return res.count if res.count is not None else 0

def fetch_next_request():
    """Gets oldest pending request if strictly under daily limit."""
    published_today = get_today_published_count()
    if published_today >= DAILY_UPLOAD_LIMIT:
        print(f"⚠️ Daily upload limit reached ({published_today}/{DAILY_UPLOAD_LIMIT}). Requests queued for next cycle.")
        return None, published_today

    res = supabase.table("news_requests").select("*").eq("status", "pending").order("created_at", desc=False).limit(1).execute()
    if res.data and len(res.data) > 0:
        return res.data[0], published_today
    return None, published_today

def download_pexels_video(keyword, index):
    url = f"https://api.pexels.com/videos/search?query={keyword}&per_page=3&orientation=portrait"
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        data = res.json()
        if not data.get("videos"):
            res = requests.get("https://api.pexels.com/videos/search?query=dark+news+abstract&per_page=3&orientation=portrait", headers=headers)
            data = res.json()
        video_data = random.choice(data["videos"])
        video_url = video_data.get("video_files", [{}])[0].get("link")
        for f in video_data.get("video_files", []):
            if f.get("height") == 1920 or f.get("quality") == "hd":
                video_url = f["link"]
                break
        if not video_url: return None
        target_path = os.path.join(DOWNLOADS_DIR, f"clip_{index}.mp4")
        v_res = requests.get(video_url, stream=True)
        with open(target_path, "wb") as f:
            for chunk in v_res.iter_content(chunk_size=1024*1024):
                if chunk: f.write(chunk)
        return target_path
    except Exception as e:
        print(f"❌ Pexels download error: {e}")
        return None

# ==========================================
# 4. AI SCRIPT GENERATOR (GEMINI 2.5)
# ==========================================
def generate_ai_script(user_topic):
    """Generates news report script + chunked editing timeline using Gemini AI."""
    print(f"🤖 Generating AI ground report for topic: '{user_topic}'...")
    prompt = f"""
You are the lead investigative writer for 'OpenWire India', an independent news outlet.
User Requested Topic: "{user_topic}"

TASK:
1. Write a high-retention 60-to-90 second news script explaining the facts, context, and ground reality of this topic.
2. The first sentence MUST be an intense curiosity hook.
3. Break the script down into timeline segments (4.0 to 6.0 seconds each) for dynamic video editing.
4. Assign a descriptive cinematic Pexels video search keyword to each segment (vertical format style).

Return STRICTLY a raw JSON object, no markdown blocks:
{{
  "title": "Viral Cliffhanger Title",
  "full_script": "Full continuous voiceover script text...",
  "timeline": [
    {{"text": "Hook sentence here...", "duration": 5.0, "keyword": "india street crowd"}},
    {{"text": "Second segment sentence...", "duration": 4.5, "keyword": "dark documents paper"}}
  ]
}}
"""
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt
        )
        json_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(json_text)
    except Exception as e:
        print(f"❌ AI Script Generation failed: {e}")
        return None

# ==========================================
# 5. SUBTITLE & FFMPEG ENGINE
# ==========================================
def create_ass_subtitles(timeline):
    ass_header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,68,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,30,30,240,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    current_time = 0.0
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        f.write(ass_header)
        for seg in timeline:
            words = seg['text'].split()
            if not words:
                current_time += seg['duration']
                continue
            duration_per_word = seg['duration'] / len(words)
            for word in words:
                word_start = current_time
                word_end = current_time + duration_per_word
                f.write(f"Dialogue: 0,{format_ass_time(word_start)},{format_ass_time(word_end)},Default,,0,0,0,,{word.upper()}\n")
                current_time = word_end

def render_ffmpeg_reel(downloaded_clips, voiceover_path, output_path):
    inputs = []
    filter_nodes = ""
    fade_dur = 0.3
    num_clips = len(downloaded_clips)
    pad_per_clip = ((num_clips - 1) * fade_dur) / num_clips if num_clips > 1 else 0.0

    for i, clip in enumerate(downloaded_clips):
        extract_dur = clip['duration'] + pad_per_clip
        inputs.append(f"-stream_loop -1 -t {extract_dur} -i '{clip['path']}'")
        filter_nodes += f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,format=yuv420p[v{i}];"

    vo_idx = num_clips
    bg_idx = vo_idx + 1
    inputs.append(f"-i '{voiceover_path}'")

    music_files = glob.glob(os.path.join(MUSIC_DIR, "*.mp3"))
    if music_files:
        inputs.append(f"-stream_loop -1 -i '{random.choice(music_files)}'")
    else:
        inputs.append(f"-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100")

    if num_clips > 1:
        last_v = "[v0]"
        current_time = downloaded_clips[0]['duration'] + pad_per_clip
        for i in range(1, num_clips):
            dur = downloaded_clips[i]['duration'] + pad_per_clip
            offset = max(0, current_time - fade_dur)
            out_v = f"[v_xf{i}]" if i < num_clips - 1 else "[video_faded]"
            filter_nodes += f"{last_v}[v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset}{out_v};"
            last_v = out_v
            current_time = offset + dur
        final_v_node = "[video_faded]"
    else:
        final_v_node = "[v0]"

    filter_nodes += f"{final_v_node}ass='{SUBS_FILE}'[video_subbed];"
    filter_nodes += f"[{vo_idx}:a]volume=1.5[vo_clean];"
    filter_nodes += f"[{bg_idx}:a]volume=0.04[bg_ducked];"
    filter_nodes += f"[vo_clean][bg_ducked]amix=inputs=2:duration=first[audio_mixed]"

    input_str = " ".join(inputs)
    cmd = f"ffmpeg -y {input_str} -filter_complex \"{filter_nodes}\" -map '[video_subbed]' -map '[audio_mixed]' -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac '{output_path}' -loglevel error"
    return os.system(cmd)

# ==========================================
# 6. INSTAGRAM PUBLISHER API
# ==========================================
def publish_to_instagram(video_public_url, caption):
    if not INSTAGRAM_ACCOUNT_ID or not META_ACCESS_TOKEN:
        print("⚠️ Meta API tokens missing. Skipping direct Instagram deployment.")
        return False

    container_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_ACCOUNT_ID}/media"
    payload = {
        'media_type': 'REELS',
        'video_url': video_public_url,
        'caption': f"{caption}\n\n#OpenWireIndia #UnspokenNews #GroundReport #India",
        'access_token': META_ACCESS_TOKEN
    }
    res = requests.post(container_url, data=payload).json()
    creation_id = res.get('id')
    
    if not creation_id:
        print("❌ Error creating Meta media container:", res)
        return False

    time.sleep(30) # Wait for Meta video processing

    publish_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_ACCOUNT_ID}/media_publish"
    pub_res = requests.post(publish_url, data={'creation_id': creation_id, 'access_token': META_ACCESS_TOKEN}).json()
    print("🚀 Published to Instagram Reel ID:", pub_res.get('id'))
    return True

# ==========================================
# 7. PIPELINE EXECUTION ENGINE
# ==========================================
def process_single_request(req):
    print(f"\n🎬 Processing Request ID: {req['id']} | Type: {req['type']}")
    supabase.table("news_requests").update({"status": "processing"}).eq("id", req['id']).execute()

    try:
        if req['type'] == 'topic':
            # 1. AI Expands topic to full script
            ai_data = generate_ai_script(req['content'])
            if not ai_data:
                raise Exception("Failed to generate script via AI")

            script_text = ai_data["full_script"]
            timeline = ai_data["timeline"]

            # 2. Synthesize TTS Voiceover
            asyncio.run(generate_voiceover(script_text, VOICEOVER_FILE))
            audio = MP3(VOICEOVER_FILE)
            actual_duration = audio.info.length

            # 3. Scale segment durations to fit speech rate
            sum_pred = sum([seg["duration"] for seg in timeline])
            scale_factor = actual_duration / sum_pred if sum_pred > 0 else 1
            for seg in timeline:
                seg["duration"] *= scale_factor

            # 4. Download footage clips
            downloaded_clips = []
            for idx, seg in enumerate(timeline):
                clip_path = download_pexels_video(seg["keyword"], idx)
                if clip_path:
                    downloaded_clips.append({"path": clip_path, "duration": seg["duration"]})

            # 5. Create Subtitles & Render Video
            create_ass_subtitles(timeline)
            exit_code = render_ffmpeg_reel(downloaded_clips, VOICEOVER_FILE, FINAL_OUTPUT)

        elif req['type'] == 'raw_video':
            # Handle user uploaded video directly
            raw_url = req['video_url']
            raw_local = os.path.join(DOWNLOADS_DIR, "raw_user_video.mp4")
            
            # Download raw video from Supabase Storage
            with requests.get(raw_url, stream=True) as r:
                with open(raw_local, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            # Fast single clip timeline
            clips = [{"path": raw_local, "duration": 15.0}]
            asyncio.run(generate_voiceover(req['content'], VOICEOVER_FILE))
            
            timeline = [{"text": req['content'], "duration": 15.0}]
            create_ass_subtitles(timeline)
            exit_code = render_ffmpeg_reel(clips, VOICEOVER_FILE, FINAL_OUTPUT)

        if exit_code == 0:
            print("🎉 Render complete!")
            
            # Upload final rendered video to Supabase rendered-reels bucket
            rendered_filename = f"render_{req['id']}.mp4"
            with open(FINAL_OUTPUT, 'rb') as f:
                supabase.storage.from_("rendered-reels").upload(rendered_filename, f, file_options={"content-type": "video/mp4"})

            public_url = f"{SUPABASE_URL}/storage/v1/object/public/rendered-reels/{rendered_filename}"
            
            # Publish to Instagram
            publish_to_instagram(public_url, req['content'])

            # Mark as published in Supabase
            supabase.table("news_requests").update({"status": "published"}).eq("id", req['id']).execute()
            print(f"✅ Request {req['id']} marked as PUBLISHED.")
        else:
            print("❌ Render failed during FFmpeg execution.")
            supabase.table("news_requests").update({"status": "failed"}).eq("id", req['id']).execute()

    except Exception as e:
        print(f"❌ Execution error: {e}")
        supabase.table("news_requests").update({"status": "failed"}).eq("id", req['id']).execute()

    finally:
        # JANITOR CLEANUP
        print("🧹 Cleaning local temp files...")
        if os.path.exists(VOICEOVER_FILE): os.remove(VOICEOVER_FILE)
        if os.path.exists(SUBS_FILE): os.remove(SUBS_FILE)
        if os.path.exists(FINAL_OUTPUT): os.remove(FINAL_OUTPUT)
        for f in glob.glob(os.path.join(DOWNLOADS_DIR, "*")):
            try: os.remove(f)
            except: pass

# ==========================================
# 8. MAIN ENTRY POINT
# ==========================================
def main():
    print("🚀 OpenWire India Production Engine initialized.")
    req, count = fetch_next_request()
    if req:
        print(f"📋 Found pending request. Daily Progress: {count + 1}/{DAILY_UPLOAD_LIMIT}")
        process_single_request(req)
    else:
        print("💤 No pending requests to process or daily quota reached.")

if __name__ == "__main__":
    main()

