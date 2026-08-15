import os
import sys
import subprocess
import glob
import json
import random
import asyncio
import time
import re
import io
import socket
from datetime import datetime, timedelta
import requests
import edge_tts
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFont
from supabase import create_client, Client
from dotenv import load_dotenv

# --- AI & GOOGLE SDKs ---
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseDownload

# Force Python to wait up to 5 minutes before timing out network connections
socket.setdefaulttimeout(300)

# --- PATH & DIRECTORY CONFIGURATION ---
BASE_DIR = os.getenv("GITHUB_WORKSPACE", os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "videos/")
DOWNLOADS_DIR = os.path.join(OUTPUT_DIR, "tmp_clips/")
GDRIVE_TMP_DIR = os.path.join(OUTPUT_DIR, "gdrive_raw/")
MUSIC_DIR = os.path.join(BASE_DIR, "music/")

VOICEOVER_FILE = os.path.join(OUTPUT_DIR, "news_voiceover.mp3")
OVERLAY_FILE = os.path.join(OUTPUT_DIR, "news_overlay.png")
FINAL_OUTPUT = os.path.join(OUTPUT_DIR, "FINAL_NEWS_VIDEO.mp4")
FONT_PATH = os.path.join(BASE_DIR, "Arial Bold.ttf")
RATE_LIMIT_FILE = os.path.join(BASE_DIR, "upload_rate_limit.json")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(GDRIVE_TMP_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)

# Environment variables
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH if os.path.exists(ENV_PATH) else None)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "X9z62cFEWZnEYQKqiRsqWvVToRNjFRx0ASNg02ZyHGtbOEjrcBaxHsW3")

# Google Drive Credentials for Long-Term Authentication
GDRIVE_REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN")
GDRIVE_CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID")
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")

# Native Gemini Setup
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Error: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing!")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("❌ Error: GEMINI_API_KEY missing in environment variables!")
    sys.exit(1)

# Initialize Supabase and Gemini clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
llm_client = genai.Client(api_key=GEMINI_API_KEY)

# Target Gemini Model
GEMINI_MODEL = "gemini-2.0-flash"

INDIA_VOICE = "en-IN-PrabhatNeural"
MAX_DAILY_UPLOADS = 25

# --- RATE LIMITER ---
def check_and_update_rate_limit():
    now = time.time()
    data = {"count": 0, "reset_time": now + 86400}
    
    if os.path.exists(RATE_LIMIT_FILE):
        try:
            with open(RATE_LIMIT_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass

    if now >= data.get("reset_time", 0):
        data["count"] = 0
        data["reset_time"] = now + 86400
        with open(RATE_LIMIT_FILE, "w") as f:
            json.dump(data, f)

    if data["count"] >= MAX_DAILY_UPLOADS:
        remaining_sec = int(data["reset_time"] - now)
        hours, remainder = divmod(remaining_sec, 3600)
        minutes, _ = divmod(remainder, 60)
        print(f"⏳ Rate limit reached. Tasks paused. Resume in {hours}h {minutes}m.")
        return False

    return True

def increment_upload_counter():
    data = {"count": 0, "reset_time": time.time() + 86400}
    if os.path.exists(RATE_LIMIT_FILE):
        try:
            with open(RATE_LIMIT_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    if data["count"] == 0:
        data["reset_time"] = time.time() + 86400

    data["count"] += 1
    with open(RATE_LIMIT_FILE, "w") as f:
        json.dump(data, f)
    print(f"📊 Daily Upload Count: {data['count']}/{MAX_DAILY_UPLOADS}")

# --- GOOGLE DRIVE INTEGRATION ---
def get_gdrive_service():
    if not all([GDRIVE_REFRESH_TOKEN, GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET]):
        print("⚠️ Google Drive credentials incomplete in .env!")
        return None
        
    creds = Credentials(
        token=None,
        refresh_token=GDRIVE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GDRIVE_CLIENT_ID,
        client_secret=GDRIVE_CLIENT_SECRET
    )
    return build('drive', 'v3', credentials=creds)

def download_raw_video_from_gdrive(file_id, output_path):
    print(f"📥 Downloading raw clip from Google Drive (ID: {file_id})...")
    service = get_gdrive_service()
    if not service:
        return False
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.FileIO(output_path, 'wb')
        
        downloader = MediaIoBaseDownload(fh, request, chunksize=512*1024)
        
        done = False
        while done is False:
            status, done = downloader.next_chunk(num_retries=5)
            if status:
                print(f"   Downloading... {int(status.progress() * 100)}%", end="\r")
        print("\n✅ Download complete!")
        return True
    except Exception as e:
        print(f"\n❌ Failed to download from Google Drive: {e}")
        return False

def delete_processed_file_from_gdrive(file_id):
    print(f"🗑️ Deleting processed video from Google Drive (ID: {file_id})...")
    service = get_gdrive_service()
    if not service:
        return
    try:
        service.files().delete(fileId=file_id).execute()
        print(f"✅ Successfully deleted Google Drive file {file_id}")
    except Exception as e:
        print(f"⚠️ Google Drive deletion notice: {e}")

# --- DYNAMIC PIL GRAPHICS OVERLAY GENERATOR ---
def create_dynamic_overlay(overlay_data, output_path, width=1080, height=1920):
    print("🎨 Rendering custom dynamic overlay image...")
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    gradient_start_y = int(height * 0.45)
    for y in range(gradient_start_y, height):
        alpha = int(235 * ((y - gradient_start_y) / (height - gradient_start_y)))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

    try:
        font_small = ImageFont.truetype(FONT_PATH, 36)
        font_main = ImageFont.truetype(FONT_PATH, 52)
        font_sub = ImageFont.truetype(FONT_PATH, 38)
        font_logo = ImageFont.truetype(FONT_PATH, 48)
    except Exception:
        font_small = font_main = font_sub = font_logo = ImageFont.load_default()

    current_y = int(height * 0.62)

    def draw_centered_text(text, font, fill, y_pos, bg_color=None):
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x_pos = (width - text_w) // 2
        
        if bg_color:
            padding_x, padding_y = 20, 10
            draw.rectangle(
                [x_pos - padding_x, y_pos - padding_y, x_pos + text_w + padding_x, y_pos + text_h + padding_y],
                fill=bg_color
            )
        draw.text((x_pos, y_pos), text, font=font, fill=fill)
        return y_pos + text_h + 20

    tag_text = overlay_data.get("tag", "BREAKING").upper()
    main_highlight = overlay_data.get("main_highlight", "MAJOR NEWS UPDATE").upper()
    main_sub = overlay_data.get("sub_text", "DEVELOPING STORY IN REAL TIME").upper()
    caption_line = overlay_data.get("caption_line", "OpenWire India coverage")
    brand_logo = overlay_data.get("brand", "truth.").lower()

    current_y = draw_centered_text(tag_text, font_small, (255, 255, 255, 255), current_y)
    current_y = draw_centered_text(main_highlight, font_main, (255, 255, 255, 255), current_y, bg_color=(180, 20, 20, 255))
    current_y = draw_centered_text(main_sub, font_main, (255, 255, 255, 255), current_y)
    current_y = draw_centered_text(caption_line, font_sub, (220, 220, 220, 255), current_y)
    draw_centered_text(brand_logo, font_logo, (255, 255, 255, 255), current_y + 10)

    img.save(output_path, "PNG")

# --- UGUU.SE EXCLUSIVE HOSTING ---
def upload_to_uguu(file_path):
    print("📤 Uploading video to Uguu.se for Meta Reel ingestion...")
    
    for attempt in range(1, 4):
        try:
            print(f"   [Attempt {attempt}/3] Uploading via native curl...")
            cmd = [
                "curl", "-s", "-X", "POST",
                "https://uguu.se/upload.php",
                "-F", f"files[]=@{file_path}",
                "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "--connect-timeout", "30",
                "--max-time", "120"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            
            if data.get("success") and len(data.get("files", [])) > 0:
                url = data["files"][0]["url"]
                print(f"✅ Uguu.se upload URL: {url}")
                return url
        except Exception as e:
            print(f"   ⚠️ curl attempt {attempt} failed: {e}")
            time.sleep(3)

    try:
        print("   [Fallback] Uploading via Python requests with Chrome headers...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        with open(file_path, "rb") as f:
            res = requests.post(
                "https://uguu.se/upload.php",
                files={"files[]": f},
                headers=headers,
                timeout=(30, 120)
            )
        data = res.json()
        if data.get("success") and len(data.get("files", [])) > 0:
            url = data["files"][0]["url"]
            print(f"✅ Uguu.se upload URL: {url}")
            return url
    except Exception as e:
        print(f"   ⚠️ Python requests fallback failed: {e}")

    print("❌ Uguu.se upload failed after all retry attempts.")
    return None

# --- INSTAGRAM PUBLISHING ENGINE ---
def publish_to_instagram(video_url, caption):
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        return None
    print("🚀 Publishing video URL to Instagram API...")
    container_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    payload = {"media_type": "REELS", "video_url": video_url, "caption": caption, "access_token": IG_ACCESS_TOKEN}
    
    res = requests.post(container_url, data=payload).json()
    if "id" not in res:
        print(f"❌ IG Container creation failed: {res}")
        return None
        
    creation_id = res["id"]
    print("⏳ IG Container created. Waiting for Meta to finish processing...")
    
    for _ in range(15): 
        time.sleep(10)
        status_res = requests.get(f"https://graph.facebook.com/v19.0/{creation_id}?fields=status_code&access_token={IG_ACCESS_TOKEN}").json()
        status = status_res.get("status_code")
        if status == "FINISHED":
            break
        elif status == "ERROR":
            print("❌ Meta processing error.")
            return None

    pub_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN}).json()
    return pub_res.get("id")

# --- PEXELS MULTI-CLIP FETCHING ---
def download_pexels_video(keyword, index):
    url = f"https://api.pexels.com/videos/search?query={keyword}&per_page=5&orientation=portrait"
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=15).json()
        if not res.get("videos"):
            res = requests.get("https://api.pexels.com/videos/search?query=breaking+news+city&per_page=3&orientation=portrait", headers=headers).json()
            
        video_data = random.choice(res["videos"])
        video_url = video_data.get("video_files", [{}])[0].get("link")
        for f in video_data.get("video_files", []):
            if f.get("quality") == "hd" and f.get("width", 0) >= 720:
                video_url = f["link"]
                break
                
        target_path = os.path.join(DOWNLOADS_DIR, f"clip_{index}.mp4")
        v_res = requests.get(video_url, stream=True)
        with open(target_path, "wb") as f:
            for chunk in v_res.iter_content(chunk_size=1024*1024):
                if chunk: f.write(chunk)
        return target_path
    except Exception as e:
        print(f"⚠️ Pexels fetch error: {e}")
        return None

async def generate_voiceover(text, output_path):
    communicate = edge_tts.Communicate(text, INDIA_VOICE, rate="+2%", pitch="-1Hz")
    await communicate.save(output_path)

def clear_temporary_build_files(gdrive_file_id=None):
    print("🧹 Cleaning local temporary build files...")
    if os.path.exists(VOICEOVER_FILE): os.remove(VOICEOVER_FILE)
    if os.path.exists(OVERLAY_FILE): os.remove(OVERLAY_FILE)
    if os.path.exists(FINAL_OUTPUT): os.remove(FINAL_OUTPUT)
    
    for f in glob.glob(os.path.join(DOWNLOADS_DIR, "*")):
        try: os.remove(f)
        except: pass

    for f in glob.glob(os.path.join(GDRIVE_TMP_DIR, "*")):
        try: os.remove(f)
        except: pass

    if gdrive_file_id:
        delete_processed_file_from_gdrive(gdrive_file_id)

# --- MAIN BATCH RENDERING ENGINE ---
def process_renders():
    if not check_and_update_rate_limit():
        return

    clusters = supabase.table("topic_clusters").select("*").eq("status", "pending").execute().data
    if not clusters: return

    print(f"\n🎬 Action Render Triggered: Processing {len(clusters)} distinct topics.")

    for cluster in clusters:
        if not check_and_update_rate_limit():
            break

        c_id = cluster["id"]
        c_name = cluster['cluster_name']
        raw_gdrive_id = cluster.get('gdrive_raw_file_id')

        supabase.table("topic_clusters").update({"status": "processing"}).eq("id", c_id).execute()

        print(f"[1/7] Browsing live web to research: '{c_name}'...")
        research_prompt = f"""
        You are an investigative news editor for OpenWire India. 
        Search the web for breaking updates regarding: "{c_name}".
        Write an engaging, fast-paced news narration script.
        RULES:
        - ABSOLUTE MAXIMUM 75 WORDS. If you exceed 75 words you will fail.
        - Must take EXACTLY 45 seconds when spoken steadily.
        - Must be written in smooth Hinglish (Hindi written in Roman script mixed with English).
        - Direct hook in the first line.
        - Ends EXACTLY with the phrase "OpenWire India." and nothing else.
        - NO URLs, NO links, NO asterisks, NO markdown, NO sources, NO extra text. 
        - OUTPUT STRICTLY THE SPOKEN NARRATION ONLY.
        """
        
        script_content = ""
        try:
            response = llm_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=research_prompt
            )
            script_content = response.text.strip()
            
            script_content = re.sub(r'http\S+', '', script_content) 
            script_content = script_content.replace('www.', '').replace('.com', '') 
            
            if "OpenWire India." in script_content:
                script_content = script_content.split("OpenWire India.")[0] + "OpenWire India."
            elif "OpenWire India" in script_content:
                script_content = script_content.split("OpenWire India")[0] + "OpenWire India."
                
            script_content = script_content.strip()

        except Exception as e:
            print(f"❌ Script generation failed: {e}")
            supabase.table("topic_clusters").update({"status": "failed"}).eq("id", c_id).execute()
            continue

        print("[2/7] Generating Dynamic Screen Overlay Text and Multi-Clip Timeline Map...")
        mapping_prompt = f"""
        Based on this news narration:
        "{script_content}"

        1. Generate dynamic poster overlay headline text:
           - "tag": Short 1-word tag
           - "main_highlight": Max 4-5 words primary headline
           - "sub_text": Max 5-6 words secondary line
           - "caption_line": 1 descriptive short line
           - "brand": "OpenWire."

        2. Create 10 to 12 fast-paced sequential visual keywords for stock video search.

        Return STRICT RAW JSON only (no markdown):
        {{
          "overlay": {{
             "tag": "WATCH",
             "main_highlight": "MAJOR PROTEST IN DELHI",
             "sub_text": "POLICE BRACING FOR MARCH",
             "caption_line": "Students demand immediate policy reform",
             "brand": "OpenWire."
          }},
          "keywords": ["delhi street crowd", "police officers", "protest banner"],
          "tags": ["#OpenWireIndia", "#News"]
        }}
        """

        mapping_data = None
        try:
            res = llm_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=mapping_prompt
            )
            raw_content = res.text.strip()
            json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            clean_json = json_match.group(0) if json_match else raw_content.replace("```json", "").replace("```", "").strip()
            mapping_data = json.loads(clean_json, strict=False)
        except Exception as e:
            print(f"❌ JSON Mapping failed: {e}")
            supabase.table("topic_clusters").update({"status": "failed"}).eq("id", c_id).execute()
            continue

        print("[3/7] Rendering voiceover & poster image overlay...")
        create_dynamic_overlay(mapping_data["overlay"], OVERLAY_FILE)
        asyncio.run(generate_voiceover(script_content, VOICEOVER_FILE))
        
        raw_audio_dur = MP3(VOICEOVER_FILE).info.length
        target_duration = min(raw_audio_dur, 50.0)
        print(f"⏱️ Voiceover duration: {raw_audio_dur:.2f}s (Hard capped video length to {target_duration:.2f}s)")

        print("[4/7] Downloading footage clips...")
        downloaded_clips = []
        has_raw_video = False
        
        if raw_gdrive_id:
            raw_local_path = os.path.join(GDRIVE_TMP_DIR, f"user_raw_{c_id}.mp4")
            if download_raw_video_from_gdrive(raw_gdrive_id, raw_local_path):
                downloaded_clips.append(raw_local_path)
                has_raw_video = True

        if not has_raw_video:
            keywords = mapping_data.get("keywords", ["news street", "crowd", "city"])
            for idx, kw in enumerate(keywords):
                clip = download_pexels_video(kw, idx)
                if clip:
                    downloaded_clips.append(clip)

        if not downloaded_clips:
            print("❌ No background footage available. Skipping.")
            supabase.table("topic_clusters").update({"status": "failed"}).eq("id", c_id).execute()
            continue

        print("[5/7] Assembling video with FFmpeg Engine...")
        inputs = []
        filter_nodes = ""
        fade_dur = 0.4
        num_clips = len(downloaded_clips)
        dur_per_clip = target_duration / num_clips
        extract_dur = dur_per_clip + fade_dur

        for i, clip_path in enumerate(downloaded_clips):
            inputs.append(f"-stream_loop -1 -t {extract_dur} -i '{clip_path}'")
            filter_nodes += f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,format=yuv420p[v{i}];"

        overlay_idx = num_clips
        inputs.append(f"-loop 1 -t {target_duration} -i '{OVERLAY_FILE}'")

        vo_idx = num_clips + 1
        inputs.append(f"-i '{VOICEOVER_FILE}'")

        bg_idx = vo_idx + 1
        music_files = glob.glob(os.path.join(MUSIC_DIR, "*.mp3"))
        if music_files:
            inputs.append(f"-stream_loop -1 -i '{random.choice(music_files)}'")
        else:
            inputs.append("-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100")

        if num_clips > 1:
            last_v = "[v0]"
            current_t = dur_per_clip
            for i in range(1, num_clips):
                out_v = f"[v_xf{i}]" if i < num_clips - 1 else "[video_faded]"
                filter_nodes += f"{last_v}[v{i}]xfade=transition=fade:duration={fade_dur}:offset={current_t}{out_v};"
                last_v = out_v
                current_t += dur_per_clip
            final_bg = "[video_faded]"
        else:
            final_bg = "[v0]"

        filter_nodes += f"{final_bg}[{overlay_idx}:v]overlay=0:0[final_video];"
        filter_nodes += f"[{vo_idx}:a]volume=1.4[vo_clean];"
        filter_nodes += f"[{bg_idx}:a]volume=0.08[bg_ducked];"
        filter_nodes += f"[vo_clean][bg_ducked]amix=inputs=2:duration=first[audio_mixed]"

        input_str = " ".join(inputs)
        cmd = f"ffmpeg -y {input_str} -filter_complex \"{filter_nodes}\" -map '[final_video]' -map '[audio_mixed]' -c:v libx264 -crf 28 -preset ultrafast -pix_fmt yuv420p -c:a aac -t {target_duration} '{FINAL_OUTPUT}' -loglevel error"

        if os.system(cmd) != 0:
            print("❌ FFmpeg layout engine error.")
            supabase.table("topic_clusters").update({"status": "failed"}).eq("id", c_id).execute()
            continue

        print("[6/7] Uploading to Uguu.se for Meta publishing...")
        public_video_url = upload_to_uguu(FINAL_OUTPUT)

        if public_video_url:
            desc = f"{c_name}\n\n{' '.join(mapping_data.get('tags', ['#News', '#OpenWire']))}"
            ig_post_id = publish_to_instagram(public_video_url, desc)

            if ig_post_id:
                print(f"🎉 Published to Instagram successfully! Post ID: {ig_post_id}")
                supabase.table("topic_clusters").update({"status": "published"}).eq("id", c_id).execute()
                supabase.table("news_requests").update({"status": "published"}).eq("cluster_id", c_id).execute()
                increment_upload_counter()
                clear_temporary_build_files(gdrive_file_id=raw_gdrive_id)
            else:
                supabase.table("topic_clusters").update({"status": "failed"}).eq("id", c_id).execute()
                clear_temporary_build_files() 
        else:
            supabase.table("topic_clusters").update({"status": "failed"}).eq("id", c_id).execute()
            clear_temporary_build_files() 

def process_unclustered_requests():
    unclustered = supabase.table("news_requests").select("*").eq("status", "pending").is_("cluster_id", "null").execute().data
    if not unclustered: return

    print(f"\n🧠 Found {len(unclustered)} raw submissions. Grouping topics...")
    active_clusters = supabase.table("topic_clusters").select("*").eq("status", "pending").execute().data

    for req in unclustered:
        cluster_info = "\n".join([f"- ID: {c['id']} | Topic: {c['cluster_name']}" for c in active_clusters])
        prompt = f"""
        Determine if this submission matches an existing topic cluster or requires a new one.
        User Submission: "{req['content']}"
        Existing Open Clusters: {cluster_info if active_clusters else "None"}
        Return STRICT RAW JSON: {{"action": "EXISTING" or "NEW", "cluster_id": "UUID or null", "cluster_name": "topic title if NEW, else null"}}
        """
        try:
            res = llm_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            raw_content = res.text.strip()
            json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            clean_json = json_match.group(0) if json_match else raw_content.replace("```json", "").replace("```", "").strip()
            result = json.loads(clean_json, strict=False)
            
            gdrive_id = req.get("video_url")

            if result.get("action") == "EXISTING" and result.get("cluster_id"):
                c_id = result["cluster_id"]
                supabase.table("news_requests").update({"cluster_id": c_id}).eq("id", req["id"]).execute()
                
                if gdrive_id:
                    supabase.table("topic_clusters").update({"gdrive_raw_file_id": gdrive_id}).eq("id", c_id).execute()
            else:
                new_topic_name = result.get("cluster_name", req["content"][:30])
                new_c = supabase.table("topic_clusters").insert({
                    "cluster_name": new_topic_name,
                    "request_count": 1,
                    "status": "pending",
                    "gdrive_raw_file_id": gdrive_id 
                }).select().execute().data[0]
                supabase.table("news_requests").update({"cluster_id": new_c["id"]}).eq("id", req["id"]).execute()
                active_clusters.append(new_c)
        except Exception as e:
            print(f"⚠️ Clustering error: {e}")

if __name__ == "__main__":
    print("📡 OpenWire Worker Started (GitHub Actions Mode)...")
    try:
        process_unclustered_requests()
        
        pending = supabase.table("topic_clusters").select("id").eq("status", "pending").execute().data
        if pending: 
            process_renders()
        else:
            print("✅ No pending renders found. Exiting cleanly.")
            
    except Exception as e:
        print(f"⚠️ Worker Error: {e}")
        sys.exit(1)
