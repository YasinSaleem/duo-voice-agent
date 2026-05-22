import express, { Request, Response, NextFunction } from 'express';
import 'dotenv/config';
import sessionRoutes from './routes/sessions';
import livekitRoutes from './routes/livekit';
import authRoutes from './routes/auth';

import path from 'path';

const app = express();

// Middleware to parse incoming JSON payloads and capture raw body buffer
app.use(express.json({
  type: ['application/json', 'application/webhook+json'],
  verify: (req: any, _res, buf) => {
    req.rawBody = buf;
  }
}));

// Request logging middleware
app.use((req: Request, res: Response, next: NextFunction) => {
  console.log(`[API Request] ${req.method} ${req.originalUrl}`);
  next();
});

// Serve static UI assets from the public directory
app.use(express.static(path.join(__dirname, '../public')));

// Session Routes
app.use('/v1/sessions', sessionRoutes);

// Auth Routes
app.use('/v1/auth', authRoutes);

// LiveKit Webhook Route
app.use('/internal/livekit', livekitRoutes);

// Browser Remote Debug Logging Endpoint
app.post('/debug-log', (req: any, res: any) => {
  const { type, message, args } = req.body;
  console.log(`[Browser ${type.toUpperCase()}]:`, message, args ? args.join(' ') : '');
  res.json({ ok: true });
});

// Health check endpoint
app.get('/health', (_req: Request, res: Response) => {
  res.json({ ok: true });
});

// Standardized 404 handler
app.use((_req: Request, res: Response) => {
  res.status(404).json({
    error: {
      code: 'NOT_FOUND',
      message: 'The requested resource or route was not found.'
    }
  });
});

// Standardized Global Error Handler Middleware
app.use((err: any, _req: Request, res: Response, _next: NextFunction) => {
  console.error('[API Global Error Handler]:', err);

  // Handle Express malformed JSON payloads
  if (err instanceof SyntaxError && 'status' in err && (err as any).status === 400) {
    res.status(400).json({
      error: {
        code: 'BAD_REQUEST',
        message: 'Invalid JSON payload received.'
      }
    });
    return;
  }

  res.status(err.status || 500).json({
    error: {
      code: err.code || 'INTERNAL_SERVER_ERROR',
      message: err.message || 'An unexpected internal server error occurred.'
    }
  });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`API server successfully started on port ${PORT}`);
});
