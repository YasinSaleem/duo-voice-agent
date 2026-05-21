import { Request, Response, NextFunction } from 'express';
import { supabaseAuthClient } from '../db/supabase';
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
    const { data: { user }, error } = await supabaseAuthClient.auth.getUser(token);

    if (error || !user) {
      res.status(401).json({
        error: {
          code: 'UNAUTHORIZED',
          message: error?.message || 'Invalid or expired authorization token.'
        }
      });
      return;
    }

    // Attach user information to request (cast to AuthenticatedRequest which guarantees req.user)
    (req as AuthenticatedRequest).user = { id: user.id };
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
