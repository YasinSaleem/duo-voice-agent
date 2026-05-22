import { Request, Response, NextFunction } from 'express';
import { supabaseAuthClient, supabaseAdmin } from '../db/supabase';
import { AuthenticatedRequest } from '../types';

export async function authMiddleware(
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> {
  const authHeader = req.headers.authorization;

  if (!authHeader) {
    res.status(401).json({
      error: {
        code: 'UNAUTHORIZED',
        message: 'Authorization header is missing.'
      }
    });
    return;
  }

  if (!authHeader.startsWith('Bearer ')) {
    res.status(401).json({
      error: {
        code: 'UNAUTHORIZED',
        message: 'Malformed Authorization header. Format must be "Bearer <token>".'
      }
    });
    return;
  }

  const token = authHeader.substring(7).trim();

  if (!token) {
    res.status(401).json({
      error: {
        code: 'UNAUTHORIZED',
        message: 'Token is missing from Authorization header.'
      }
    });
    return;
  }

  try {
    let user: any = null;

    if (process.env.ALLOW_DEMO_AUTH === 'true' && token === 'demo-token') {
      const { data: authUsers, error: listErr } = await supabaseAdmin.auth.admin.listUsers();
      if (listErr) {
        throw listErr;
      }
      
      let demoUser = authUsers?.users.find(u => u.email === 'demo-learner@example.com');
      if (!demoUser) {
        console.log('[Auth Middleware] Demo learner user not found. Creating default demo-learner@example.com...');
        const { data: newUser, error: createErr } = await supabaseAdmin.auth.admin.createUser({
          email: 'demo-learner@example.com',
          password: 'DemoPassword123!',
          email_confirm: true
        });
        if (createErr || !newUser || !newUser.user) {
          throw new Error(`Failed to create default demo user: ${createErr?.message}`);
        }
        demoUser = newUser.user;
      }
      user = demoUser;
    } else {
      const { data: { user: supabaseUser }, error } = await supabaseAuthClient.auth.getUser(token);
      if (error || !supabaseUser) {
        res.status(401).json({
          error: {
            code: 'UNAUTHORIZED',
            message: error?.message || 'Invalid or expired authorization token.'
          }
        });
        return;
      }
      user = supabaseUser;
    }

    if (!user) {
      res.status(401).json({
        error: {
          code: 'UNAUTHORIZED',
          message: 'Invalid or expired authorization token.'
        }
      });
      return;
    }

    // Attach user information to request (cast to AuthenticatedRequest which guarantees req.user)
    (req as AuthenticatedRequest).user = { 
      id: user.id,
      accessToken: token
    };
    next();
  } catch (err: any) {
    res.status(500).json({
      error: {
        code: 'INTERNAL_SERVER_ERROR',
        message: err.message || 'An unexpected error occurred during authentication.'
      }
    });
  }
}
