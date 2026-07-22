import { createClient } from '@supabase/supabase-js';

// Initialize Supabase on the server using hidden environment variables
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

    // Insert request directly into DB safely from server
    const { data, error } = await supabase
      .from('news_requests')
      .insert([{ type, content, video_url }]);

    if (error) throw error;

    return res.status(200).json({ success: true, message: 'Request queued successfully!' });
  } catch (error) {
    console.error('Server error:', error);
    return res.status(500).json({ error: error.message || 'Internal Server Error' });
  }
}
