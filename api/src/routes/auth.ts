import { Router, Response } from 'express';
import { createClient } from '@supabase/supabase-js';
import { authMiddleware } from '../middleware/auth';
import { AuthenticatedRequest } from '../types';

const router = Router();

/**
 * Route: GET /v1/auth/config
 * Purpose: Exposes dynamic SUPABASE_URL and SUPABASE_ANON_KEY to the frontend.
 * Why Config Route is Kept:
 * - Avoids hardcoding credentials in build artifacts, maintaining clean separation of environments.
 * - Dynamically propagates backend .env config to the frontend, eliminating sync errors.
 */
router.get('/config', (_req, res) => {
  res.json({
    supabaseUrl: process.env.SUPABASE_URL,
    supabaseAnonKey: process.env.SUPABASE_ANON_KEY
  });
});

/**
 * Route: GET /v1/auth/memories
 * Purpose: Fetch the authenticated user's memory timeline (RLS-enforced).
 */
router.get('/memories', authMiddleware, async (req: AuthenticatedRequest, res: Response): Promise<any> => {
  try {
    // Dynamic user-scoped client using the propagated accessToken
    const userClient = createClient(
      process.env.SUPABASE_URL!,
      process.env.SUPABASE_ANON_KEY!,
      {
        auth: { persistSession: false, autoRefreshToken: false },
        global: { headers: { Authorization: `Bearer ${req.user.accessToken}` } }
      }
    );

    // Queries memories respecting RLS policies
    const { data: memories, error } = await userClient
      .from('memories')
      .select('*')
      .order('created_at', { ascending: false });

    if (error) {
      console.error('[Auth] Error fetching memories:', error);
      return res.status(500).json({ error: { code: 'DATABASE_ERROR', message: 'Failed to retrieve memories.' } });
    }

    return res.status(200).json(memories);
  } catch (err: any) {
    return res.status(500).json({ error: { code: 'INTERNAL_SERVER_ERROR', message: err.message } });
  }
});

export default router;
