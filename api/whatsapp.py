import os
import requests
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse
from google import genai
from supabase import create_client

app = FastAPI()

# 1. Initialize Clients
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)


@app.post("/api/whatsapp")
async def whatsapp_handler(
    Body: str = Form(default=""),
    From: str = Form(default=""),
    NumMedia: int = Form(default=0),
    MediaUrl0: str = Form(default=None),
    MediaContentType0: str = Form(default=None)
):
    resp = MessagingResponse()
    reply = resp.message()

    sender_phone = From.replace("whatsapp:", "")
    user_text = Body.strip()
    gdrive_file_ids = []

    # -------------------------------------------------------------
    # A. HANDLE MEDIA UPLOAD TO GOOGLE DRIVE
    # -------------------------------------------------------------
    if NumMedia > 0 and MediaUrl0:
        try:
            twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
            twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
            
            # Fetch raw media stream from Twilio
            media_req = requests.get(MediaUrl0, auth=(twilio_sid, twilio_token))
            
            if media_req.status_code == 200:
                # TODO: Pass 'media_req.content' to your Google Drive upload function
                # file_id = upload_to_drive(media_req.content, MediaContentType0)
                file_id = "sample_gdrive_id"  # Placeholder
                gdrive_file_ids.append(file_id)
        except Exception as e:
            print(f"Error handling media download: {e}")

    # -------------------------------------------------------------
    # B. ANALYZE TEXT WITH GEMINI AI
    # -------------------------------------------------------------
    extracted_name = "Anonymous"
    extracted_location = "Dhubri, Assam"
    analysis_summary = user_text

    if user_text:
        try:
            prompt = f"""
            Analyze the following WhatsApp citizen journalism report text:
            "{user_text}"

            Perform 2 tasks:
            1. Check if the text explicitly contains a contributor's name. If found, return only the name. Otherwise, return 'Anonymous'.
            2. Extract location if mentioned, otherwise return 'Dhubri, Assam'.

            Format your response exactly as:
            NAME: <extracted_name>
            LOCATION: <extracted_location>
            """

            ai_response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )

            response_lines = ai_response.text.strip().split("\n")
            for line in response_lines:
                if line.startswith("NAME:"):
                    extracted_name = line.replace("NAME:", "").strip()
                elif line.startswith("LOCATION:"):
                    extracted_location = line.replace("LOCATION:", "").strip()

        except Exception as e:
            print(f"Gemini AI processing error: {e}")

    # -------------------------------------------------------------
    # C. SAVE TO SUPABASE DB
    # -------------------------------------------------------------
    try:
        supabase.table("news_requests").insert({
            "location": extracted_location,
            "contributor_name": extracted_name,
            "event_detail": user_text or "[Media evidence attached]",
            "gdrive_file_ids": gdrive_file_ids,
            "status": "pending"
        }).execute()
    except Exception as e:
        print(f"Supabase write error: {e}")

    # -------------------------------------------------------------
    # D. REPLY VIA WHATSAPP
    # -------------------------------------------------------------
    reply.body(
        f"📰 *Lok Press Ground Report Received!*\n\n"
        f"• *Contributor:* {extracted_name}\n"
        f"• *Location:* {extracted_location}\n\n"
        f"Thank you! Our newsroom is reviewing your submission."
    )

    return Response(content=str(resp), media_type="application/xml")
          
