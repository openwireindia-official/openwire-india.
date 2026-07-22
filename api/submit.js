import { createClient } from '@supabase/supabase-js';

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY
);

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const { type, content, video_url } = req.body;

    if (!content) {
      return res.status(400).json({ error: 'Content/description is required' });
    }

    // 1. Insert request into Supabase and select returned row ID
    const { data, error } = await supabase
      .from('news_requests')
      .insert([{ type, content, video_url }])
      .select();

    if (error) throw error;

    const requestId = data[0].id;

    // 2. Trigger GitHub Actions via Repository Dispatch
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

    return res.status(200).json({ success: true, message: 'Request queued successfully!' });
  } catch (error) {
    console.error('Server error:', error);
    return res.status(500).json({ error: error.message || 'Internal Server Error' });
  }
}
