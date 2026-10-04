import os
import sys
import subprocess
import glob
import json
import time
import re
import requests
import mimetypes
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from supabase import create_client, Client
from dotenv import load_dotenv
from google import genai

# --- PATH & DIRECTORY CONFIGURATION ---
BASE_DIR = os.getenv("GITHUB_WORKSPACE", os.path.dirname(os.path.abspath(__file__)))
TEMP_DIR = os.path.join(BASE_DIR, "temp_builds/")
FONT_PATH = os.path.join(BASE_DIR, "Arial Bold.ttf") # Ensure this font is in your repo

os.makedirs(TEMP_DIR, exist_ok=True)

# Environment variables
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH if os.path.exists(ENV_PATH) else None)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, GEMINI_API_KEY, IG_ACCESS_TOKEN, IG_USER_ID]):
    print("❌ Error: Missing critical environment variables!")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
llm_client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3.5-flash"


# ==========================================
# 1. AI EDITORIAL DESK (GEMINI)
# ==========================================
def run_ai_triage():
    """Scans pending raw reports, uses Gemini to format them and assigns risk status."""
    print("\n🕵️‍♂️ Scanning for pending citizen reports...")
    pending_reports = supabase.table("citizen_reports").select("*").eq("status", "pending").execute().data
    
    if not pending_reports:
        print("   No pending reports found.")
        return

    for report in pending_reports:
        r_id = report["id"]
        raw_text = report.get("raw_text", "")
        
        prompt = f"""
        You are the Chief Editor for OpenWire Dhubri. Review this raw citizen submission:
        "{raw_text}"

        1. FORMATTING: Rewrite this into a strict, neutral maximum 60-word news brief. No sensationalism.
        2. TRIAGE RISK:
           - GREEN: Mundane civic issues (potholes, weather, local sports, campus events).
           - YELLOW: Mentions local businesses, minor disputes, or unverified claims.
           - RED: Hate speech, defamation, severe accusations, or explicit content.

        Return STRICT RAW JSON ONLY:
        {{
          "headline": "Max 8 words",
          "dateline": "Specific location mentioned",
          "body": "The 60-word professional news summary",
          "risk_status": "GREEN"
        }}
        """
        
        try:
            res = llm_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            raw_res = res.text.strip()
            json_match = re.search(r'\{.*\}', raw_res, re.DOTALL)
            clean_json = json_match.group(0) if json_match else raw_res.replace("```json", "").replace("```", "").strip()
            editorial = json.loads(clean_json, strict=False)
            
            new_status = "approved" if editorial.get("risk_status") == "GREEN" else "review"
            if editorial.get("risk_status") == "RED": new_status = "rejected"

            supabase.table("citizen_reports").update({
                "headline": editorial.get("headline"),
                "body": editorial.get("body"),
                "dateline": editorial.get("dateline"),
                "status": new_status
            }).eq("id", r_id).execute()
            
            print(f"✅ Triage Complete [{new_status.upper()}]: {editorial.get('headline')}")
            
        except Exception as e:
            print(f"❌ AI Triage failed for {r_id}: {e}")


# ==========================================
# 2. GRAPHICS ENGINE (PIL)
# ==========================================
def draw_wrapped_text(draw, text, font, max_width, x, y, fill):
    """Helper to wrap text pixel-perfectly within the image."""
    lines = []
    words = text.split()
    current_line = []
    
    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]
    if current_line: lines.append(" ".join(current_line))
    
    current_y = y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text((x, current_y), line, font=font, fill=fill)
        current_y += bbox[3] - bbox[1] + 15 
    return current_y

def generate_transparent_overlay(headline, body, dateline, contributor, output_path):
    """Generates a 1080x1920 transparent PNG with dark gradients and text."""
    print("🎨 Generating Smart Graphics Overlay...")
    width, height = 1080, 1920
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw bottom readability gradient (Black fade)
    grad_start = int(height * 0.40)
    for y in range(grad_start, height):
        alpha = int(240 * ((y - grad_start) / (height - grad_start))) # Max 94% opacity at bottom
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
        
    # Draw top readability gradient (for dateline)
    for y in range(0, 300):
        alpha = int(180 * (1 - (y / 300)))
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

    # Load Fonts (Fallback to default if missing)
    try:
        font_tag = ImageFont.truetype(FONT_PATH, 35)
        font_head = ImageFont.truetype(FONT_PATH, 65)
        font_body = ImageFont.truetype(FONT_PATH, 42)
        font_badge = ImageFont.truetype(FONT_PATH, 38)
    except:
        font_tag = font_head = font_body = font_badge = ImageFont.load_default()

    # Draw Text Layout
    margin = 80
    max_text_width = width - (margin * 2)
    
    # Top Dateline
    draw.text((margin, 80), f"📍 {dateline.upper()}", font=font_tag, fill=(255, 100, 100, 255))
    
    # Bottom Content Structure
    current_y = int(height * 0.55)
    
    # Headline
    current_y = draw_wrapped_text(draw, headline.upper(), font_head, max_text_width, margin, current_y, (255, 255, 255, 255))
    current_y += 30 # Spacing
    
    # Body
    current_y = draw_wrapped_text(draw, body, font_body, max_text_width, margin, current_y, (220, 220, 220, 255))
    current_y += 50 # Spacing
    
    # Contributor Badge
    credit_text = f"🎙️ REPORTED BY: {contributor.upper()}"
    bbox = draw.textbbox((0, 0), credit_text, font=font_badge)
    draw.rectangle([margin - 15, current_y - 15, margin + (bbox[2]-bbox[0]) + 15, current_y + (bbox[3]-bbox[1]) + 15], fill=(255, 153, 51, 255)) # Saffron background
    draw.text((margin, current_y), credit_text, font=font_badge, fill=(255, 255, 255, 255))

    img.save(output_path, "PNG")


# ==========================================
# 3. MEDIA PROCESSING (IMAGE & VIDEO)
# ==========================================
def process_media(input_media_url, overlay_png_path, output_path):
    """Detects media type, downloads it, and composites the overlay."""
    print("⚙️ Processing & Merging Raw Media...")
    
    # Download raw media
    raw_ext = ".mp4" if "mp4" in input_media_url.lower() else ".jpg"
    raw_path = os.path.join(TEMP_DIR, f"raw_input{raw_ext}")
    
    with requests.get(input_media_url, stream=True) as r:
        r.raise_for_status()
        with open(raw_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192): f.write(chunk)

    is_video = raw_ext == ".mp4"

    if is_video:
        # FFmpeg: Center crop to 9:16, add overlay, encode to standard MP4, keep original audio
        # Using aac for audio to ensure it's standard and plays well on Instagram
        cmd = [
            "ffmpeg", "-y", "-i", raw_path, "-i", overlay_png_path,
            "-filter_complex", 
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[bg];[bg][1:v]overlay=0:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-map", "0:a?", # map 0:a? grabs audio only if it exists
            output_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "VIDEO"
    else:
        # PIL: Center crop image and paste overlay
        bg = Image.open(raw_path).convert("RGBA")
        overlay = Image.open(overlay_png_path).convert("RGBA")
        
        # Calculate aspect fill crop (1080x1920)
        target_ratio = 1080 / 1920
        bg_ratio = bg.width / bg.height
        
        if bg_ratio > target_ratio: # Too wide
            new_width = int(bg.height * target_ratio)
            left = (bg.width - new_width) / 2
            bg = bg.crop((left, 0, left + new_width, bg.height))
        else: # Too tall
            new_height = int(bg.width / target_ratio)
            top = (bg.height - new_height) / 2
            bg = bg.crop((0, top, bg.width, top + new_height))
            
        bg = bg.resize((1080, 1920), Image.Resampling.LANCZOS)
        bg.alpha_composite(overlay)
        bg.convert("RGB").save(output_path, "JPEG", quality=95)
        return "IMAGE"


# ==========================================
# 4. HOSTING & DISTRIBUTION
# ==========================================
def host_on_supabase(local_file_path, filename):
    """Uploads the rendered file to your Supabase storage to get a public URL for Instagram."""
    print(f"☁️ Uploading {filename} to Supabase Storage...")
    with open(local_file_path, "rb") as f:
        # Note: Requires a public bucket named 'openwire_renders' in Supabase
        res = supabase.storage.from_("openwire_renders").upload(filename, f, file_options={"upsert": "true"})
        if res:
            return f"{SUPABASE_URL}/storage/v1/object/public/openwire_renders/{filename}"
    return None

def publish_to_instagram(media_url, media_type, caption):
    """Routes the public URL to either the IG Image or IG Reels endpoint."""
    print("🚀 Publishing to Instagram Graph API...")
    container_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    
    if media_type == "VIDEO":
        payload = {"media_type": "REELS", "video_url": media_url, "caption": caption, "access_token": IG_ACCESS_TOKEN}
    else:
        payload = {"image_url": media_url, "caption": caption, "access_token": IG_ACCESS_TOKEN}
        
    # 1. Create Container
    res = requests.post(container_url, data=payload).json()
    if "id" not in res:
        print(f"❌ IG Container failed: {res}")
        return None
    creation_id = res["id"]
    
    # 2. Wait for processing (Crucial for Video)
    if media_type == "VIDEO":
        print("⏳ Waiting for Meta to process video...")
        for _ in range(12): 
            time.sleep(10)
            status_res = requests.get(f"https://graph.facebook.com/v19.0/{creation_id}?fields=status_code&access_token={IG_ACCESS_TOKEN}").json()
            if status_res.get("status_code") == "FINISHED": break
            if status_res.get("status_code") == "ERROR": return None

    # 3. Publish
    pub_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN}).json()
    media_id = pub_res.get("id")
    
    if media_id:
        # 4. Get Permalink URL to send back to the user
        link_res = requests.get(f"https://graph.facebook.com/v19.0/{media_id}?fields=permalink&access_token={IG_ACCESS_TOKEN}").json()
        return link_res.get("permalink")
    return None


# ==========================================
# 5. THE MAIN LOOP
# ==========================================
def cleanup_temp_files():
    for f in glob.glob(os.path.join(TEMP_DIR, "*")):
        try: os.remove(f)
        except: pass

def main():
    print("🟢 OpenWire Smart Media Engine Started...")
    
    # 1. Run Triage on new raw submissions
    run_ai_triage()
    
    # 2. Fetch approved articles ready for rendering
    approved_reports = supabase.table("citizen_reports").select("*").eq("status", "approved").execute().data
    
    for report in approved_reports:
        try:
            r_id = report["id"]
            headline = report["headline"]
            body = report["body"]
            dateline = report["dateline"]
            contributor = report["contributor_name"] or "Anonymous"
            media_url = report["media_url"]
            
            print(f"\n📰 Assembling News Card: {headline}")
            
            # Set up file paths
            overlay_path = os.path.join(TEMP_DIR, "overlay.png")
            
            # Step A: Generate Overlay
            generate_transparent_overlay(headline, body, dateline, contributor, overlay_path)
            
            # Step B: Merge Media
            final_ext = ".mp4" if "mp4" in media_url.lower() else ".jpg"
            final_path = os.path.join(TEMP_DIR, f"final_{r_id}{final_ext}")
            media_type = process_media(media_url, overlay_path, final_path)
            
            # Step C: Host on Supabase
            hosted_media_url = host_on_supabase(final_path, f"final_{r_id}{final_ext}")
            if not hosted_media_url: raise Exception("Failed to host rendered media.")
            
            # Step D: Publish to Instagram
            caption = f"{headline}\n\n{body}\n\n📍 {dateline}\n🎙️ Citizen Report by: {contributor}\n\n#OpenWireIndia #DhubriNews #CitizenJournalism"
            ig_permalink = publish_to_instagram(hosted_media_url, media_type, caption)
            
            if ig_permalink:
                print(f"🎉 Success! Live at: {ig_permalink}")
                # Update DB to signal WhatsApp Bot that it's time to notify the user
                supabase.table("citizen_reports").update({
                    "status": "published",
                    "published_url": ig_permalink
                }).eq("id", r_id).execute()
            else:
                raise Exception("Instagram API returned an error.")
                
        except Exception as e:
            print(f"❌ Error processing report {r_id}: {e}")
            supabase.table("citizen_reports").update({"status": "failed"}).eq("id", r_id).execute()
            
        finally:
            cleanup_temp_files()

if __name__ == "__main__":
    main()
                                 
