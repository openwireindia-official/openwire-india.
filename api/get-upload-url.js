import { google } from 'googleapis';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const { fileName, fileType } = req.body;

    if (!fileName) {
      return res.status(400).json({ error: 'Filename is required' });
    }

    // 1. Sanitize file name and add timestamps
    const today = new Date().toISOString().split('T')[0];
    const uniqueId = Math.random().toString(36).substring(2, 9);
    const sanitizedFileName = `${today}_${Date.now()}_${uniqueId}_${fileName.replace(/[^a-zA-Z0-9.-]/g, '_')}`;

    // 2. Authenticate using OAuth2 credentials
    const oauth2Client = new google.auth.OAuth2(
      process.env.GDRIVE_CLIENT_ID,
      process.env.GDRIVE_CLIENT_SECRET
    );

    oauth2Client.setCredentials({
      refresh_token: process.env.GDRIVE_REFRESH_TOKEN,
    });

    const { token } = await oauth2Client.getAccessToken();

    if (!token) {
      throw new Error("Failed to retrieve access token using Refresh Token.");
    }

    // 3. Build file metadata safely
    const fileMetadata = {
      name: sanitizedFileName,
    };

    if (process.env.GOOGLE_DRIVE_FOLDER_ID) {
      fileMetadata.parents = [process.env.GOOGLE_DRIVE_FOLDER_ID];
    }

    // 4. Get the domain requesting the upload URL (e.g. https://openwire-india.vercel.app)
    const clientOrigin = req.headers.origin || `https://${req.headers.host}`;

    // 5. Initiate Resumable Upload Session with Google Drive API & pass the Origin header
    const driveResponse = await fetch(
      'https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable',
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json; charset=UTF-8',
          'X-Upload-Content-Type': fileType || 'video/mp4',
          'Origin': clientOrigin // <-- THIS ALLOWS THE BROWSER TO UPLOAD DIRECTLY TO GOOGLE
        },
        body: JSON.stringify(fileMetadata),
      }
    );

    if (!driveResponse.ok) {
      const errText = await driveResponse.text();
      throw new Error(`Google Drive API Error: ${errText}`);
    }

    // 6. Retrieve direct upload URI from Location header
    const uploadUrl = driveResponse.headers.get('location');

    return res.status(200).json({
      uploadUrl: uploadUrl,
      fileName: sanitizedFileName,
    });

  } catch (error) {
    console.error('Error creating Google Drive session:', error);
    return res.status(500).json({ error: error.message || 'Server error' });
  }
}
