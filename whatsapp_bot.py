import os
import json
import requests
import io
from fastapi import FastAPI, Request, Response
from supabase import create_client, Client
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaIoBaseUpload

# --- ENVIRONMENT CONFIGURATION ---
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")  # Meta Cloud API Access Token
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID") # WhatsApp Phone Number ID
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "lokpress_secure_token")

# Google Drive Credentials
GDRIVE_REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN")
GDRIVE_CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID")
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID") # Optional: specific folder for raw uploads

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

# In-memory session tracker for users currently being prompted for their name
# In production, you can store this in a Supabase table or Redis
USER_SESSIONS = {}

# --- GOOGLE DRIVE HELPER ---
def get_gdrive_service():
    creds = Credentials(
        token=None,
        refresh_token=GDRIVE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GDRIVE_CLIENT_ID,
        client_secret=GDRIVE_CLIENT_SECRET
    )
    return build('drive', 'v3', credentials=creds)

def upload_bytes_to_gdrive(file_bytes, filename, mime_type):
    service = get_gdrive_service()
    file_metadata = {'name': filename}
    if GDRIVE_FOLDER_ID:
        file_metadata['parents'] = [GDRIVE_FOLDER_ID]
        
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    
    # Make file accessible or shareable if needed by worker
    return file.get('id')

# --- WHATSAPP MESSAGING HELPER ---
def send_whatsapp_message(to_phone, message):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message}
    }
    requests.post(url, headers=headers, json=payload)

def download_whatsapp_media(media_id):
    # 1. Get media URL from WhatsApp API
    url_res = requests.get(
        f"https://graph.facebook.com/v19.0/{media_id}",
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    ).json()
    media_url = url_res.get("url")
    mime_type = url_res.get("mime_type", "image/jpeg")
    
    # 2. Download binary content
    media_data = requests.get(
        media_url,
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    ).content
    return media_data, mime_type

# --- WEBHOOK ENDPOINTS ---
@app.get("/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, status_code=200)
    return Response(content="Verification failed", status_code=403)

@app.post("/webhook")
async def handle_incoming_whatsapp(request: Request):
    body = await request.json()
    
    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            return {"status": "ok"}
            
        msg = messages[0]
        sender_phone = msg.get("from")
        msg_type = msg.get("type")
        
        # Check if user is in an active session waiting for name input
        if sender_phone in USER_SESSIONS and msg_type == "text":
            name_input = msg.get("text", {}).get("body", "").strip()
            session = USER_SESSIONS.pop(sender_phone)
            
            contributor_name = "Anonymous" if name_input.lower() in ["anonymous", "anon", "no"] else name_input
            
            # Finalize insert into Supabase
            supabase.table("news_requests").insert({
                "location": session["location"],
                "contributor_name": contributor_name,
                "event_detail": session["event_detail"],
                "gdrive_file_ids": json.dumps(session["gdrive_file_ids"]),
                "status": "pending"
            }).execute()
            
            send_whatsapp_message(
                sender_phone, 
                f"✅ Thank you! Your report has been submitted successfully with credit to: *{contributor_name}*. Our editorial system is processing it for Lok Press."
            )
            return {"status": "ok"}

        # Handle new incoming media submissions
        if msg_type in ["image", "video"]:
            media_obj = msg.get(msg_type)
            media_id = media_obj.get("id")
            caption = media_obj.get("caption", "Breaking news update from citizen")
            
            send_whatsapp_message(sender_phone, "📥 Receiving your media file... Uploading to secure storage.")
            
            # Download from WhatsApp and upload to Google Drive
            file_bytes, mime_type = download_whatsapp_media(media_id)
            ext = ".mp4" if "video" in mime_type else ".jpg"
            gdrive_file_id = upload_bytes_to_gdrive(file_bytes, f"lokpress_{sender_phone}{ext}", mime_type)
            
            # Store temporary session data, default location to Dhubri
            USER_SESSIONS[sender_phone] = {
                "event_detail": caption,
                "location": "Dhubri", 
                "gdrive_file_ids": [gdrive_file_id]
            }
            
            # Prompt for Name / Anonymity
            send_whatsapp_message(
                sender_phone, 
                "📰 Media received!\n\nWould you like your name displayed on the official **Lok Lens** badge for this report, or do you want to remain Anonymous?\n\n👉 *Reply with your name or type 'Anonymous'.*"
            )
            
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
  
