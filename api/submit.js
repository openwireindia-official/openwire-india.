import { createClient } from '@supabase/supabase-js';
import { Ratelimit } from '@upstash/ratelimit';
import { Redis } from '@upstash/redis';
import { google } from 'googleapis';

const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_ROLE_KEY);
const ratelimit = new Ratelimit({ redis: Redis.fromEnv(), limiter: Ratelimit.slidingWindow(3, '1 h'), analytics: true });

function fetchWithTimeout(url, options, timeoutMs = 10000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timeout));
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  try {
    const ip = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || req.socket?.remoteAddress || '127.0.0.1';
    const { success } = await ratelimit.limit(`submit_${ip}`);
    if (!success) return res.status(429).json({ error: 'Too many reports submitted. Please try again later.' });

    const { location = 'Unspecified', contributor_name = 'Anonymous', event_detail, content, upload_tokens = [], turnstile_token } = req.body || {};
    if (!turnstile_token) return res.status(400).json({ error: 'Bot verification token is missing.' });

    const verifyRes = await fetchWithTimeout('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ secret: process.env.TURNSTILE_SECRET_KEY, response: turnstile_token, remoteip: ip })
    });
    if (!verifyRes.ok) return res.status(502).json({ error: 'Bot verification service unavailable.' });
    const verifyData = await verifyRes.json();
    if (!verifyData.success) return res.status(403).json({ error: 'Bot verification failed.' });

    const finalDetail = typeof (event_detail || content) === 'string' ? (event_detail || content).trim() : '';
    if (!finalDetail) return res.status(400).json({ error: 'Content/description is required.' });
    if (finalDetail.length > 5000 || /\u0000/.test(finalDetail)) return res.status(400).json({ error: 'Event details are invalid.' });
    if (typeof location !== 'string' || typeof contributor_name !== 'string') return res.status(400).json({ error: 'Invalid text field.' });
    if (!Array.isArray(upload_tokens) || upload_tokens.length > 1) return res.status(400).json({ error: 'Invalid upload list.' });

    const redis = Redis.fromEnv();
    const verifiedFileIds = [];
    if (upload_tokens.length) {
      const token = upload_tokens[0];
      if (typeof token !== 'string' || !/^[a-f0-9]{32}$/.test(token)) return res.status(400).json({ error: 'Invalid upload token.' });
      const rawUpload = await redis.get(`upload:${token}`);
      if (!rawUpload) return res.status(400).json({ error: 'Upload token is invalid or expired.' });
      let uploadData;
      try { uploadData = typeof rawUpload === 'string' ? JSON.parse(rawUpload) : rawUpload; } catch { return res.status(400).json({ error: 'Invalid upload token.' }); }

      const client = new google.auth.OAuth2(process.env.GDRIVE_CLIENT_ID, process.env.GDRIVE_CLIENT_SECRET);
      client.setCredentials({ refresh_token: process.env.GDRIVE_REFRESH_TOKEN });
      const drive = google.drive({ version: 'v3', auth: client });
      const fileId = typeof req.body.gdrive_file_id === 'string' ? req.body.gdrive_file_id : null;
      if (!fileId || !/^[a-zA-Z0-9_-]{10,100}$/.test(fileId)) return res.status(400).json({ error: 'Invalid uploaded file.' });
      const metadata = await drive.files.get({ fileId, fields: 'name,mimeType' });
      if (!metadata.data?.name || metadata.data.name !== uploadData.fileName) return res.status(400).json({ error: 'Upload ownership could not be verified.' });
      const allowed = ['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/quicktime', 'video/webm'];
      if (!allowed.includes(metadata.data.mimeType)) return res.status(415).json({ error: 'Unsupported uploaded media type.' });
      verifiedFileIds.push(fileId);
      await redis.del(`upload:${token}`);
    }

    const { data, error } = await supabase.from('news_requests').insert([{
      location: location.trim().slice(0, 150),
      contributor_name: contributor_name.trim().slice(0, 100),
      event_detail: finalDetail,
      gdrive_file_ids: verifiedFileIds,
      status: 'awaiting_approval'
    }]).select();
    if (error) {
      console.error('Supabase insert failed:', error.code || 'unknown_error');
      return res.status(503).json({ error: 'Report could not be queued. Please try again.' });
    }

    const requestId = data[0].id;
    if (process.env.GITHUB_PAT && process.env.GITHUB_OWNER && process.env.GITHUB_REPO) {
      try {
        await fetchWithTimeout(`https://api.github.com/repos/${process.env.GITHUB_OWNER}/${process.env.GITHUB_REPO}/dispatches`, {
          method: 'POST', headers: { Authorization: `Bearer ${process.env.GITHUB_PAT}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json' },
          body: JSON.stringify({ event_type: 'process_news_request', client_payload: { request_id: requestId } })
        });
      } catch (error) {
        console.error('GitHub dispatch failed:', error.name || 'unknown_error');
      }
    }

    return res.status(200).json({ success: true, message: 'Request queued for editorial review.', id: requestId });
  } catch (error) {
    console.error('Submission handler failed:', error?.name || 'unknown_error');
    return res.status(500).json({ error: 'Submission could not be completed.' });
  }
}
