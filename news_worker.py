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
FONT_PATH = os.path.join(BASE_DIR, "Arial Bold.ttf")

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

# --- VECTOR-BASED LOK LENS OVERLAY ENGINE ---
def create_lok_press_overlay(headline, location, contributor, output_path, width=1080, height=1920):
    print("🎨 Rendering Safe-Zone Lok Lens Overlay...")
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Bottom Readability Gradient
    grad_start = int(height * 0.50)
    for y in range(grad_start, height):
        alpha = int(240 * ((y - grad_start) / (height - grad_start)))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

    # 2. Fonts Engine
    try:
        font_alert = ImageFont.truetype(FONT_PATH, 38)
        font_head = ImageFont.truetype(FONT_PATH, 55)
        font_brand = ImageFont.truetype(FONT_PATH, 42)
        font_meta = ImageFont.truetype(FONT_PATH, 34)
        font_badge = ImageFont.truetype(FONT_PATH, 34)
    except:
        font_alert = font_head = font_brand = font_meta = font_badge = ImageFont.load_default()

    # =========================================================
    # 3. VECTOR LOK LENS BADGE (SAFE-ZONE POSITION)
    # =========================================================
    badge_label = f"LOK LENS • {contributor.upper()}"
    bbox = draw.textbbox((0, 0), badge_label, font=font_badge)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    icon_size = 32
    padding_x = 35
    padding_y = 18
    gap = 16

    total_content_w = icon_size + gap + text_w + gap + icon_size
    box_w = total_content_w + (padding_x * 2)
    box_h = max(text_h, icon_size) + (padding_y * 2)

    # Position: y1 = 280 keeps it completely clear of Instagram's top header UI bar
    x1 = (width - box_w) // 2
    y1 = 280 
    x2 = x1 + box_w
    y2 = y1 + box_h

    # Translucent White Pill Container
    draw.rounded_rectangle(
        [x1, y1, x2, y2],
        radius=box_h // 2,
        fill=(255, 255, 255, 235),
        outline=(210, 215, 225, 255),
        width=3
    )

    # A. Draw Vector Eye Icon (Blue)
    eye_x = x1 + padding_x
    eye_y = y1 + (box_h - icon_size) // 2
    draw.ellipse([eye_x, eye_y + 6, eye_x + icon_size, eye_y + icon_size - 6], outline=(13, 110, 253, 255), width=3)
    draw.ellipse([eye_x + 11, eye_y + 11, eye_x + 21, eye_y + 21], fill=(13, 110, 253, 255))

    # B. Draw Contributor Text
    text_x = eye_x + icon_size + gap
    text_y = y1 + (box_h - text_h) // 2 - 2
    draw.text((text_x, text_y), badge_label, font=font_badge, fill=(13, 110, 253, 255))

    # C. Draw Vector Verified Checkmark Circle
    chk_x = text_x + text_w + gap
    chk_y = y1 + (box_h - icon_size) // 2
    draw.ellipse([chk_x, chk_y, chk_x + icon_size, chk_y + icon_size], fill=(13, 110, 253, 255))
    draw.line([(chk_x + 8, chk_y + 16), (chk_x + 14, chk_y + 22)], fill=(255, 255, 255, 255), width=4)
    draw.line([(chk_x + 14, chk_y + 22), (chk_x + 24, chk_y + 10)], fill=(255, 255, 255, 255), width=4)

    # =========================================================
    # 4. LOWER-THIRD HEADLINE & BRANDING
    # =========================================================
    def draw_centered_text(text, font, fill, y_pos, bg_color=None):
        bbox_t = draw.textbbox((0, 0), text, font=font)
        tw = bbox_t[2] - bbox_t[0]
        th = bbox_t[3] - bbox_t[1]
        x_pos = (width - tw) // 2
        
        if bg_color:
            pad_x, pad_y = 25, 14
            draw.rectangle([x_pos - pad_x, y_pos - pad_y, x_pos + tw + pad_x, y_pos + th + pad_y], fill=bg_color)
        draw.text((x_pos, y_pos), text, font=font, fill=fill)
        return y_pos + th + 30

    current_y = int(height * 0.65)

    current_y = draw_centered_text("ALERT", font_alert, (255, 255, 255, 255), current_y)

    current_y = draw_centered_text(
        headline.upper(),
        font_head,
        fill=(255, 255, 255, 255),
        y_pos=current_y,
        bg_color=(200, 30, 30, 255)
    )

    location_text = f"LOCATION: {location.upper()}"
    current_y = draw_centered_text(location_text, font_meta, (220, 220, 220, 255), current_y)
    
    draw_centered_text("lok press.", font_brand, (255, 255, 255, 255), current_y)

    img.save(output_path, "PNG")

# --- MEDIA PROCESSING ---
def process_media(downloaded_files, overlay_path):
    print("⚙️ Processing media (Padding & Overlays)...")
    
    videos = [f for f in downloaded_files if f.endswith(".mp4")]
    images = [f for f in downloaded_files if f.endswith(".jpg")]
    
    if videos:
        input_vid = videos[0]
        cmd = [
            "ffmpeg", "-y", "-i", input_vid, "-i", overlay_path,
            "-filter_complex", 
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[bg];[bg][1:v]overlay=0:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-map", "0:a?", FINAL_OUTPUT
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return FINAL_OUTPUT, "VIDEO"

    elif len(images) > 1:
        concat_file = os.path.join(GDRIVE_TMP_DIR, "slides.txt")
        with open(concat_file, "w") as f:
            for img in images:
                f.write(f"file '{os.path.abspath(img)}'\n")
                f.write("duration 3\n")
            f.write(f"file '{os.path.abspath(images[-1])}'\n")
            
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file, "-i", overlay_path,
            "-filter_complex",
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black[bg];[bg][1:v]overlay=0:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", FINAL_OUTPUT
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return FINAL_OUTPUT, "VIDEO"

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

def upload_to_uguu(file_path):
    print("📤 Uploading media to Uguu.se...")
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

def main():
    print("📡 Lok Press Automation Started...")
    requests_data = supabase.table("news_requests").select("*").eq("status", "pending").execute().data
    
    if not requests_data:
        print("✅ No pending requests found.")
        return

    for req in requests_data:
        req_id = req["id"]
        event_detail = req["event_detail"]
        location = req["location"] or "Dhubri"
        contributor = req["contributor_name"] or "Anonymous"
        gdrive_ids = req.get("gdrive_file_ids", [])
        
        if isinstance(gdrive_ids, str): gdrive_ids = json.loads(gdrive_ids)

        print(f"\n📰 Processing Request: {req_id}")
        supabase.table("news_requests").update({"status": "processing"}).eq("id", req_id).execute()

        try:
            prompt = f"Extract a maximum 6 word punchy news headline from this event detail: '{event_detail}'. Return strictly the headline text."
            headline = llm_client.models.generate_content(model=GEMINI_MODEL, contents=prompt).text.strip().replace('"', '')

            downloaded_files = []
            for idx, gid in enumerate(gdrive_ids):
                path, mtype = download_gdrive_file(gid, os.path.join(GDRIVE_TMP_DIR, f"media_{idx}"))
                if path: downloaded_files.append(path)

            if not downloaded_files:
                raise Exception("No valid media could be downloaded.")

            create_lok_press_overlay(headline, location, contributor, OVERLAY_FILE)

            final_media_path, media_type = process_media(downloaded_files, OVERLAY_FILE)

            public_url = upload_to_uguu(final_media_path)
            if not public_url: raise Exception("Failed to host media externally.")

            caption = f"{headline}\n\n{event_detail}\n\n📍 {location}\n🎙️ Reported by: {contributor}\n\n#LokPress #Dhubri #News"
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
