import express, { Request, Response, NextFunction } from 'express';
import 'dotenv/config';
import sessionRoutes from './routes/sessions';

const app = express();

// Middleware to parse incoming JSON payloads
app.use(express.json());

// Session Routes
app.use('/v1/sessions', sessionRoutes);

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
