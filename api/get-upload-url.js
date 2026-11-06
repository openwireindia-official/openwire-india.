import { google } from 'googleapis';
import { Redis } from '@upstash/redis';

const ALLOWED_TYPES = new Set([
  'image/jpeg', 'image/png', 'image/webp',
  'video/mp4', 'video/quicktime', 'video/webm'
]);
const MAX_FILE_SIZE = 50 * 1024 * 1024;

function fetchWithTimeout(url, options, timeoutMs = 10000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timeout));
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  try {
    const { fileName, fileType, fileSize, turnstileToken } = req.body || {};
    if (!fileName || typeof fileName !== 'string') return res.status(400).json({ error: 'Filename is required.' });
    if (!ALLOWED_TYPES.has(fileType)) return res.status(415).json({ error: 'Unsupported media type.' });
    if (!Number.isInteger(fileSize) || fileSize < 1 || fileSize > MAX_FILE_SIZE) {
      return res.status(413).json({ error: 'Media must be between 1 byte and 50 MB.' });
    }
    if (!turnstileToken) return res.status(400).json({ error: 'Bot verification is required.' });

    const origin = req.headers.origin;
    const configuredOrigins = (process.env.ALLOWED_ORIGINS || '').split(',').map(v => v.trim()).filter(Boolean);
    const hostOrigin = req.headers.host ? `${req.headers['x-forwarded-proto'] || 'https'}://${req.headers.host}` : null;
    if (configuredOrigins.length) {
      if (!origin || !configuredOrigins.includes(origin)) return res.status(403).json({ error: 'Origin is not allowed.' });
    } else if (origin && hostOrigin && origin !== hostOrigin) {
      return res.status(403).json({ error: 'Origin is not allowed.' });
    }

    const ip = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || req.socket?.remoteAddress || '127.0.0.1';
    const verifyRes = await fetchWithTimeout('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ secret: process.env.TURNSTILE_SECRET_KEY, response: turnstileToken, remoteip: ip })
    });
    if (!verifyRes.ok) return res.status(502).json({ error: 'Bot verification service unavailable.' });
    const verifyData = await verifyRes.json();
    if (!verifyData.success) return res.status(403).json({ error: 'Bot verification failed.' });

    const oauth2Client = new google.auth.OAuth2(process.env.GDRIVE_CLIENT_ID, process.env.GDRIVE_CLIENT_SECRET);
    oauth2Client.setCredentials({ refresh_token: process.env.GDRIVE_REFRESH_TOKEN });
    const { token } = await oauth2Client.getAccessToken();
    if (!token) throw new Error('OAuth access token unavailable');

    const today = new Date().toISOString().slice(0, 10);
    const uploadId = cryptoRandomId();
    const safeName = fileName.replace(/[^a-zA-Z0-9._-]/g, '_').slice(-180);
    const fileMetadata = { name: `${today}_${uploadId}_${safeName}` };
    if (process.env.GOOGLE_DRIVE_FOLDER_ID) fileMetadata.parents = [process.env.GOOGLE_DRIVE_FOLDER_ID];

    const driveResponse = await fetchWithTimeout('https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Type': fileType,
        Origin: origin || hostOrigin
      },
      body: JSON.stringify(fileMetadata)
    });
    if (!driveResponse.ok) throw new Error('Storage session could not be created');

    const uploadUrl = driveResponse.headers.get('location');
    if (!uploadUrl) throw new Error('Storage session returned no upload URL');

    const redis = Redis.fromEnv();
    await redis.set(`upload:${uploadId}`, JSON.stringify({ fileName: fileMetadata.name }), { ex: 3600 });

    return res.status(200).json({ uploadUrl, uploadToken: uploadId, fileName: fileMetadata.name });
  } catch (error) {
    console.error('Google Drive upload session error:', error);
    return res.status(500).json({ error: 'Upload preparation failed.' });
  }
}

function cryptoRandomId() {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}
