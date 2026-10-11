import os
import requests
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client

app = FastAPI()

# Initialize Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/")
def health_check():
    return {"status": "Lok Press Twilio Bot is live!"}

@app.post("/webhook")
async def twilio_whatsapp_webhook(
    Body: str = Form(default=""),
    From: str = Form(default=""),
    NumMedia: int = Form(default=0),
    MediaUrl0: str = Form(default=None),
    MediaContentType0: str = Form(default=None)
):
    resp = MessagingResponse()
    reply = resp.message()

    sender_phone = From.replace("whatsapp:", "")
    event_detail = Body.strip()

    gdrive_file_ids = []

    # 1. Handle incoming media (Photo/Video from citizen)
    if NumMedia > 0 and MediaUrl0:
        try:
            # Download media stream from Twilio using Twilio Auth
            twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
            twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
            
            media_response = requests.get(MediaUrl0, auth=(twilio_sid, twilio_token))
            
            if media_response.status_code == 200:
                # TODO: Insert your Google Drive upload helper function here 
                # which uploads 'media_response.content' and returns the Drive File ID.
                # For now, we store a placeholder or your Drive upload logic:
                drive_file_id = upload_to_google_drive(media_response.content, MediaContentType0)
                if drive_file_id:
                    gdrive_file_ids.append(drive_file_id)
        except Exception as e:
            print(f"Media download/upload error: {e}")

    # 2. Save incoming report to Supabase news_requests table
    if event_detail or gdrive_file_ids:
        try:
            supabase.table("news_requests").insert({
                "location": "Dhubri, Assam", # Can be extracted or default
                "contributor_name": "Anonymous", # Default until prompt update
                "event_detail": event_detail or "[Media Report Received]",
                "gdrive_file_ids": gdrive_file_ids,
                "status": "pending"
            }).execute()
        except Exception as e:
            print(f"Supabase insert error: {e}")

    # 3. Reply to citizen asking for attribution preference
    reply.body(
        "📰 *Lok Press Received!*\n\n"
        "Thank you for sharing this ground report. How would you like your contribution credited?\n\n"
        "Reply with your **Name** (e.g., *Rahul Amin*) or type **Anonymous**."
    )

    return Response(content=str(resp), media_type="application/xml")

def upload_to_google_drive(file_bytes, content_type):
    # Plug in your existing Google Drive service account upload logic here
    # and return the uploaded file ID string.
    pass
