import { createClient } from '@supabase/supabase-js';
import { crypto } from 'crypto';

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_ROLE_KEY
);

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const { fileName, fileType } = req.body;

    if (!fileName) {
      return res.status(400).json({ error: 'Filename is required' });
    }

    // 1. Generate organized path: evidence/YYYY-MM-DD/timestamp_uniqueID_cleanFilename
    const today = new Date().toISOString().split('T')[0];
    const uniqueId = Math.random().toString(36).substring(2, 9);
    const sanitizedFileName = fileName.replace(/[^a-zA-Z0-9.-]/g, '_');
    
    // Path inside bucket: evidence/2026-07-22/1721640000_a1b2c3d_video.mp4
    const storagePath = `evidence/${today}/${Date.now()}_${uniqueId}_${sanitizedFileName}`;

    // 2. Create signed upload URL from Supabase (valid for 10 minutes)
    const { data, error } = await supabase.storage
      .from('evidence-videos')
      .createSignedUploadUrl(storagePath);

    if (error) throw error;

    // 3. Get the public/permanent URL for DB reference later
    const { data: urlData } = supabase.storage
      .from('evidence-videos')
      .getPublicUrl(storagePath);

    return res.status(200).json({
      signedUrl: data.signedUrl,
      path: data.path,
      publicUrl: urlData.publicUrl
    });

  } catch (error) {
    console.error('Error generating signed URL:', error);
    return res.status(500).json({ error: error.message || 'Server error' });
  }
}
