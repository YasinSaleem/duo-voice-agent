import { Request } from 'express';

// Used ONLY on routes protected by authMiddleware where req.user is guaranteed to exist
export interface AuthenticatedRequest extends Request {
  user: {
    id: string;
    accessToken: string;
  };
}

export interface ApiErrorResponse {
  error: {
    code: string;
    message: string;
  };
}
