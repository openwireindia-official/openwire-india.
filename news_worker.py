import os
import sys
import subprocess
import glob
import json
import time
import re
import io
import requests
from PIL import Image, ImageDraw, ImageFont
from supabase import create_client, Client
from dotenv import load_dotenv

# --- AI & GOOGLE SDKs ---
from google import genai
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseDownload

# --- PATH & DIRECTORY CONFIGURATION ---
BASE_DIR = os.getenv("GITHUB_WORKSPACE", os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output/")
GDRIVE_TMP_DIR = os.path.join(OUTPUT_DIR, "raw_media/")

OVERLAY_FILE = os.path.join(OUTPUT_DIR, "lok_press_overlay.png")
FINAL_OUTPUT = os.path.join(OUTPUT_DIR, "FINAL_LOK_PRESS_POST.mp4")
FINAL_IMG_OUTPUT = os.path.join(OUTPUT_DIR, "FINAL_LOK_PRESS_POST.jpg")
FONT_PATH = os.path.join(BASE_DIR, "Arial Bold.ttf") # Ensure this font is present

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(GDRIVE_TMP_DIR, exist_ok=True)

# Environment variables
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH if os.path.exists(ENV_PATH) else None)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Google Drive Credentials
GDRIVE_REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN")
GDRIVE_CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID")
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")

if not all([SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY]):
    print("❌ Error: Missing critical environment variables!")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
llm_client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3.5-flash"

# --- GOOGLE DRIVE INTEGRATION ---
def get_gdrive_service():
    creds = Credentials(
        token=None,
        refresh_token=GDRIVE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GDRIVE_CLIENT_ID,
        client_secret=GDRIVE_CLIENT_SECRET
    )
    return build('drive', 'v3', credentials=creds)

def download_gdrive_file(file_id, output_path):
    print(f"📥 Downloading raw media from Google Drive (ID: {file_id})...")
    service = get_gdrive_service()
    try:
        # Get file metadata to determine extension
        file_metadata = service.files().get(fileId=file_id, fields='mimeType').execute()
        mime_type = file_metadata.get('mimeType', '')
        
        ext = ".mp4" if "video" in mime_type else ".jpg"
        final_path = output_path + ext
        
        request = service.files().get_media(fileId=file_id)
        fh = io.FileIO(final_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request, chunksize=1024*1024)
        
        done = False
        while not done:
            status, done = downloader.next_chunk(num_retries=3)
        return final_path, "video" if ext == ".mp4" else "image"
    except Exception as e:
        print(f"❌ Failed to download from Google Drive: {e}")
        return None, None

# --- LOK PRESS GRAPHICS OVERLAY ---
def create_lok_press_overlay(headline, location, contributor, output_path, width=1080, height=1920):
    print("🎨 Rendering Lok Press overlay...")
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Bottom gradient for text readability
    grad_start = int(height * 0.50)
    for y in range(grad_start, height):
        alpha = int(240 * ((y - grad_start) / (height - grad_start)))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

    try:
        font_alert = ImageFont.truetype(FONT_PATH, 40)
        font_head = ImageFont.truetype(FONT_PATH, 55)
        font_brand = ImageFont.truetype(FONT_PATH, 45)
        font_meta = ImageFont.truetype(FONT_PATH, 35)
    except:
        font_alert = font_head = font_brand = font_meta = ImageFont.load_default()

    def draw_centered_text(text, font, fill, y_pos, bg_color=None):
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x_pos = (width - text_w) // 2
        
        if bg_color:
            pad_x, pad_y = 25, 15
            draw.rectangle([x_pos - pad_x, y_pos - pad_y, x_pos + text_w + pad_x, y_pos + text_h + pad_y], fill=bg_color)
        draw.text((x_pos, y_pos), text, font=font, fill=fill)
        return y_pos + text_h + 30

    # Layout matches the reference image structure
    current_y = int(height * 0.65)
    
    # 1. ALERT Tag
    current_y = draw_centered_text("ALERT", font_alert, (255, 255, 255, 255), current_y)
    
    # 2. Main Headline (Red Box)
    current_y = draw_centered_text(headline.upper(), font_head, (255, 255, 255, 255), current_y, bg_color=(200, 30, 30, 255))
    
    # 3. Location & Brand
    current_y = draw_centered_text(f"📍 {location.upper()}", font_meta, (220, 220, 220, 255), current_y)
    draw_centered_text("lok press.", font_brand, (255, 255, 255, 255), current_y)
    
    # 4. Contributor Badge (Top Left or Top Center)
    draw.text((50, 80), f"Reported by: {contributor}", font=font_meta, fill=(255, 200, 50, 255))

    img.save(output_path, "PNG")

# --- MEDIA PROCESSING (FFMPEG) ---
def process_media(downloaded_files, overlay_path):
    print("⚙️ Processing media (Padding & Overlays)...")
    
    videos = [f for f in downloaded_files if f.endswith(".mp4")]
    images = [f for f in downloaded_files if f.endswith(".jpg")]
    
    # CASE 1: Single Video
    if videos:
        input_vid = videos[0]
        # Pad to 9:16, keep original audio, overlay text
        cmd = [
            "ffmpeg", "-y", "-i", input_vid, "-i", overlay_path,
            "-filter_complex", 
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[bg];[bg][1:v]overlay=0:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-map", "0:a?", FINAL_OUTPUT
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return FINAL_OUTPUT, "VIDEO"

    # CASE 2: Multiple Photos (Slideshow Video)
    elif len(images) > 1:
        # Create a text file for ffmpeg concat demuxer
        concat_file = os.path.join(GDRIVE_TMP_DIR, "slides.txt")
        with open(concat_file, "w") as f:
            for img in images:
                f.write(f"file '{os.path.abspath(img)}'\n")
                f.write("duration 3\n") # 3 seconds per photo
            f.write(f"file '{os.path.abspath(images[-1])}'\n") # Repeat last to prevent truncation
            
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-i", overlay_path,
            "-filter_complex",
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[bg];[bg][1:v]overlay=0:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", FINAL_OUTPUT
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return FINAL_OUTPUT, "VIDEO"

    # CASE 3: Single Photo
    elif len(images) == 1:
        bg = Image.open(images[0]).convert("RGBA")
        overlay = Image.open(overlay_path).convert("RGBA")
        
        bg.thumbnail((1080, 1920), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 255))
        
        paste_x = (1080 - bg.width) // 2
        paste_y = (1920 - bg.height) // 2
        canvas.paste(bg, (paste_x, paste_y))
        
        canvas.alpha_composite(overlay)
        canvas.convert("RGB").save(FINAL_IMG_OUTPUT, "JPEG", quality=95)
        return FINAL_IMG_OUTPUT, "IMAGE"
        
    return None, None

# --- UGUU.SE HOSTING & IG PUBLISHING ---
def upload_to_uguu(file_path):
    print("📤 Uploading media to Uguu.se for Meta ingestion...")
    cmd = [
        "curl", "-s", "-X", "POST", "https://uguu.se/upload.php",
        "-F", f"files[]=@{file_path}",
        "-A", "Mozilla/5.0"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    if data.get("success"):
        return data["files"][0]["url"]
    return None

def publish_to_instagram(media_url, media_type, caption):
    print("🚀 Publishing to Instagram...")
    container_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    
    if media_type == "VIDEO":
        payload = {"media_type": "REELS", "video_url": media_url, "caption": caption, "access_token": IG_ACCESS_TOKEN}
    else:
        payload = {"image_url": media_url, "caption": caption, "access_token": IG_ACCESS_TOKEN}
        
    res = requests.post(container_url, data=payload).json()
    creation_id = res.get("id")
    if not creation_id: return None
    
    if media_type == "VIDEO":
        for _ in range(12): 
            time.sleep(10)
            status_res = requests.get(f"https://graph.facebook.com/v19.0/{creation_id}?fields=status_code&access_token={IG_ACCESS_TOKEN}").json()
            if status_res.get("status_code") == "FINISHED": break

    pub_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN}).json()
    return pub_res.get("id")

def cleanup():
    for f in glob.glob(os.path.join(GDRIVE_TMP_DIR, "*")): os.remove(f)
    if os.path.exists(OVERLAY_FILE): os.remove(OVERLAY_FILE)
    if os.path.exists(FINAL_OUTPUT): os.remove(FINAL_OUTPUT)
    if os.path.exists(FINAL_IMG_OUTPUT): os.remove(FINAL_IMG_OUTPUT)

# --- MAIN LOOP ---
def main():
    print("📡 Lok Press Automation Started...")
    requests_data = supabase.table("news_requests").select("*").eq("status", "pending").execute().data
    
    if not requests_data:
        print("✅ No pending requests found.")
        return

    for req in requests_data:
        req_id = req["id"]
        event_detail = req["event_detail"]
        location = req["location"] or "Local News"
        contributor = req["contributor_name"] or "Anonymous"
        gdrive_ids = req.get("gdrive_file_ids", [])
        
        if isinstance(gdrive_ids, str): gdrive_ids = json.loads(gdrive_ids)

        print(f"\n📰 Processing Request: {req_id}")
        supabase.table("news_requests").update({"status": "processing"}).eq("id", req_id).execute()

        try:
            # 1. AI Headline Generation
            prompt = f"Extract a maximum 6 word punchy news headline from this event detail: '{event_detail}'. Return strictly the headline text."
            headline = llm_client.models.generate_content(model=GEMINI_MODEL, contents=prompt).text.strip().replace('"', '')

            # 2. Download Media
            downloaded_files = []
            for idx, gid in enumerate(gdrive_ids):
                path, mtype = download_gdrive_file(gid, os.path.join(GDRIVE_TMP_DIR, f"media_{idx}"))
                if path: downloaded_files.append(path)

            if not downloaded_files:
                raise Exception("No valid media could be downloaded.")

            # 3. Create Graphic Overlay
            create_lok_press_overlay(headline, location, contributor, OVERLAY_FILE)

            # 4. Render Media
            final_media_path, media_type = process_media(downloaded_files, OVERLAY_FILE)

            # 5. Host & Publish
            public_url = upload_to_uguu(final_media_path)
            if not public_url: raise Exception("Failed to host media externally.")

            caption = f"{headline}\n\n{event_detail}\n\n📍 {location}\n🎙️ Reported by: {contributor}\n\n#LokPress #News"
            ig_id = publish_to_instagram(public_url, media_type, caption)
            
            if ig_id:
                permalink = f"https://instagram.com/p/{ig_id}"
                print(f"🎉 Success! Published at: {permalink}")
                supabase.table("news_requests").update({"status": "published", "published_url": permalink}).eq("id", req_id).execute()
            else:
                raise Exception("Instagram API error.")

        except Exception as e:
            print(f"❌ Error processing {req_id}: {e}")
            supabase.table("news_requests").update({"status": "failed"}).eq("id", req_id).execute()
        finally:
            cleanup()

if __name__ == "__main__":
    main()
        
