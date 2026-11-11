import { createClient } from '@supabase/supabase-js';
import { Ratelimit } from '@upstash/ratelimit';
import { Redis } from '@upstash/redis';

// Initialize Supabase Client
const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY
);

// Initialize Upstash Rate Limiter (3 requests per 1 hour window per IP)
const ratelimit = new Ratelimit({
  redis: Redis.fromEnv(),
  limiter: Ratelimit.slidingWindow(3, "1 h"),
  analytics: true,
});

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    // 1. Enforce IP Rate Limiting
    const ip = req.headers['x-forwarded-for']?.split(',')[0] || req.socket.remoteAddress || '127.0.0.1';
    const { success: rateLimitPassed } = await ratelimit.limit(`submit_${ip}`);

    if (!rateLimitPassed) {
      return res.status(429).json({ 
        error: 'Too many reports submitted from your IP address. Please try again in an hour.' 
      });
    }

    const { 
      location = 'Unspecified', 
      contributor_name = 'Anonymous', 
      event_detail, 
      content, 
      gdrive_file_ids = [],
      turnstile_token 
    } = req.body;

    // 2. Verify Cloudflare Turnstile Token server-side
    if (!turnstile_token) {
      return res.status(400).json({ error: 'Bot verification token is missing.' });
    }

    const verifyRes = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        secret: process.env.TURNSTILE_SECRET_KEY,
        response: turnstile_token,
        remoteip: ip
      })
    });

    const verifyData = await verifyRes.json();
    if (!verifyData.success) {
      return res.status(403).json({ error: 'Bot verification failed. Please try again.' });
    }

    // Support both event_detail and content fields
    const finalDetail = event_detail || content;

    if (!finalDetail || !finalDetail.trim()) {
      return res.status(400).json({ error: 'Content/description is required' });
    }

    if (finalDetail.length > 5000) {
      return res.status(400).json({ error: 'Event details exceed maximum allowed length.' });
    }

    // 3. Insert into Supabase matching your schema safely
    const { data, error } = await supabase
      .from('news_requests')
      .insert([
        {
          location: location ? location.slice(0, 150) : 'Unspecified',
          contributor_name: contributor_name ? contributor_name.slice(0, 100) : 'Anonymous',
          event_detail: finalDetail.trim(),
          gdrive_file_ids: Array.isArray(gdrive_file_ids) ? gdrive_file_ids : [],
          status: 'pending'
        }
      ])
      .select();

    if (error) {
      console.error('Supabase Insert Error:', error);
      throw error;
    }

    const requestId = data[0].id;

    // 4. Trigger GitHub Actions via Repository Dispatch (worker trigger)
    if (process.env.GITHUB_PAT && process.env.GITHUB_OWNER && process.env.GITHUB_REPO) {
      const ghResponse = await fetch(
        `https://api.github.com/repos/${process.env.GITHUB_OWNER}/${process.env.GITHUB_REPO}/dispatches`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${process.env.GITHUB_PAT}`,
            'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            event_type: 'process_news_request',
            client_payload: { request_id: requestId }
          }),
        }
      );

      if (!ghResponse.ok) {
        console.warn("GitHub Action trigger returned non-200 status, but row was queued.");
      }
    }

    return res.status(200).json({ success: true, message: 'Request queued successfully!', id: requestId });
  } catch (error) {
    console.error('Server error:', error);
    return res.status(500).json({ error: error.message || 'Internal Server Error' });
  }
}
