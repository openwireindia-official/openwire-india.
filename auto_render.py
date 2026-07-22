import os
import requests
from supabase import create_client, Client
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, ImageClip

# 1. Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_pending_requests():
    """Fetch unprocessed news requests from Supabase"""
    response = supabase.table("news_requests").select("*").eq("status", "pending").execute()
    return response.data

def download_file(url, local_filename):
    """Download video from Supabase Storage"""
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(local_filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return local_filename

def render_openwire_reel(video_path, description, output_path):
    """Overlay OpenWire India branding & captions on raw video"""
    clip = VideoFileClip(video_path)

    # Force 9:16 vertical aspect ratio (1080x1920)
    clip = clip.resize(height=1920)
    if clip.width > 1080:
        clip = clip.crop(x_center=clip.width/2, width=1080)

    # Overlay OpenWire Header Banner
    header_text = TextClip(
        "OPENWIRE INDIA | GROUND REPORT", 
        fontsize=40, color='orange', font='Arial-Bold', 
        bg_color='black', size=(1000, 100)
    ).set_position(('center', 100)).set_duration(clip.duration)

    # Subtitle / Description Overlay at bottom
    caption_text = TextClip(
        description[:120] + "...", 
        fontsize=32, color='white', font='Arial', 
        bg_color='rgba(0,0,0,0.7)', size=(960, 200), method='caption'
    ).set_position(('center', 1600)).set_duration(clip.duration)

    # Combine video with overlays
    final = CompositeVideoClip([clip, header_text, caption_text])
    final.write_videofile(output_path, codec="libx264", audio_codec="aac", fps=30)

def process_queue():
    requests_list = fetch_pending_requests()
    print(f"Found {len(requests_list)} pending request(s).")

    for req in requests_list:
        print(f"Processing ID: {req['id']} | Type: {req['type']}")
        
        if req['type'] == 'raw_video' and req.get('video_url'):
            raw_file = f"temp_{req['id']}.mp4"
            rendered_file = f"openwire_render_{req['id']}.mp4"

            # Download & Render
            download_file(req['video_url'], raw_file)
            render_openwire_reel(raw_file, req['content'], rendered_file)

            print(f"✅ Render complete: {rendered_file}")

            # Update DB status
            supabase.table("news_requests").update({"status": "rendered"}).eq("id", req['id']).execute()

            # Cleanup temp raw file
            if os.path.exists(raw_file):
                os.remove(raw_file)

if __name__ == "__main__":
    process_queue()
  
