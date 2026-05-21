import * as dotenv from 'dotenv';
import * as path from 'path';
import * as fs from 'fs';
import { createClient } from '@supabase/supabase-js';
import { MongoClient } from 'mongodb';
import { RoomServiceClient } from 'livekit-server-sdk';
import { Redis } from '@upstash/redis';
import ws from 'ws';

// Colors for formatting
const colors = {
  ok: '\x1b[32m[OK]\x1b[0m',
  warn: '\x1b[33m[WARN]\x1b[0m',
  fail: '\x1b[31m[FAIL]\x1b[0m',
  info: '\x1b[36m[INFO]\x1b[0m',
  bold: '\x1b[1m',
  reset: '\x1b[0m',
};

// Mask sensitive credentials in MongoDB URI
function maskMongoUri(uri: string): string {
  const match = uri.match(/^(mongodb(?:\+srv)?:\/\/)([^/\s?]+)/i);
  if (!match) return 'mongodb://***@unknown...';
  const scheme = match[1];
  const authority = match[2];
  
  const atIndex = authority.lastIndexOf('@');
  const host = atIndex !== -1 ? authority.substring(atIndex + 1) : authority;
  
  let maskedHost = host;
  const clusterMatch = host.match(/^(cluster)/i);
  if (clusterMatch) {
    maskedHost = clusterMatch[1] + '...';
  } else if (host.length > 10) {
    maskedHost = host.substring(0, 7) + '...';
  }
  
  return `${scheme}***@${maskedHost}`;
}

// Sanitize URL by removing credentials if any are present
function sanitizeUrl(url: string): string {
  const cleanUrl = url.replace(/^([a-zA-Z+.-]+:\/\/)(?:[^@]+)@(.*)$/i, '$1$2');
  const match = cleanUrl.match(/^([a-zA-Z+.-]+:\/\/)([^/]+)/i);
  if (!match) return url;
  
  const protocol = match[1];
  const host = match[2];
  
  if (host.toLowerCase().endsWith('.upstash.io')) {
    return `${protocol}***.upstash.io`;
  }
  
  const parts = host.split('.');
  if (parts.length > 2) {
    return `${protocol}***.${parts.slice(1).join('.')}`;
  }
  
  return `${protocol}***`;
}

// Mask sensitive environment variable values to prevent leakage in error outputs
function maskSecrets(text: string): string {
  let masked = text;
  masked = masked.replace(/(mongodb(?:\+srv)?:\/\/[^:]+:)([^@]+)(@)/ig, '$1***$3');
  for (const key of ['SUPABASE_SERVICE_ROLE_KEY', 'LIVEKIT_API_SECRET', 'UPSTASH_REDIS_REST_TOKEN', 'MONGO_URI']) {
    const secret = process.env[key];
    if (secret && secret.length > 4) {
      masked = masked.split(secret).join('***');
    }
  }
  return masked;
}

// Help / Usage
const verbose = process.argv.includes('--verbose');

// Load environment variables
const envPath = path.resolve(__dirname, '../.env');
if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath });
} else {
  dotenv.config(); // fallback
}

console.log(`${colors.info} ${colors.bold}Starting API Environment Diagnostics...${colors.reset}\n`);

// Config Validation Rules
const urlRegex = /^https?:\/\/[^\s$.?#].[^\s]*$/i;
const mongoRegex = /^mongodb(\+srv)?:\/\/.+/i;
const wsRegex = /^(wss?|https?):\/\/[^\s$.?#].[^\s]*$/i; // LiveKit URLs can be ws/wss/http/https

interface EnvVar {
  key: string;
  required: boolean;
  validate?: (val: string) => boolean;
  formatError?: string;
}

const expectedEnvVars: EnvVar[] = [
  {
    key: 'SUPABASE_URL',
    required: true,
    validate: (val) => urlRegex.test(val),
    formatError: 'Must be a valid HTTPS URL',
  },
  {
    key: 'SUPABASE_SERVICE_ROLE_KEY',
    required: true,
  },
  {
    key: 'MONGO_URI',
    required: true,
    validate: (val) => mongoRegex.test(val),
    formatError: 'Must be a valid MongoDB URI (starting with mongodb:// or mongodb+srv://)',
  },
  {
    key: 'LIVEKIT_URL',
    required: true,
    validate: (val) => wsRegex.test(val),
    formatError: 'Must be a valid ws/wss or http/https URL',
  },
  {
    key: 'LIVEKIT_API_KEY',
    required: true,
  },
  {
    key: 'LIVEKIT_API_SECRET',
    required: true,
  },
  {
    key: 'UPSTASH_REDIS_REST_URL',
    required: true,
    validate: (val) => urlRegex.test(val),
    formatError: 'Must be a valid HTTP/HTTPS URL',
  },
  {
    key: 'UPSTASH_REDIS_REST_TOKEN',
    required: true,
  },
];

let validationFailed = false;
const values: Record<string, string> = {};

console.log(`${colors.bold}--- Environment Variables Check ---${colors.reset}`);
for (const envVar of expectedEnvVars) {
  const val = process.env[envVar.key];
  if (!val) {
    console.log(`${colors.fail} ${envVar.key} is missing`);
    validationFailed = true;
    continue;
  }
  
  if (envVar.validate && !envVar.validate(val)) {
    console.log(`${colors.fail} ${envVar.key} has invalid format. ${envVar.formatError}`);
    validationFailed = true;
    continue;
  }

  values[envVar.key] = val;
  console.log(`${colors.ok} ${envVar.key} is set and format is valid`);
}

if (validationFailed) {
  console.log(`\n${colors.fail} ${colors.bold}Environment validation failed. Please fix your .env file before running diagnostics.${colors.reset}`);
  process.exit(1);
}

console.log(`\n${colors.bold}--- External Services Connectivity Checks ---${colors.reset}`);

let exitCode = 0;

function logOk(msg: string) {
  console.log(`${colors.ok} ${msg}`);
}

function logWarn(msg: string) {
  console.log(`${colors.warn} ${msg}`);
}

function logFail(msg: string, err?: any) {
  console.log(`${colors.fail} ${msg}`);
  if (err) {
    if (verbose) {
      const stack = err.stack || String(err);
      console.error(maskSecrets(stack));
    } else {
      console.error(`  Reason: ${maskSecrets(err.message || String(err))}`);
    }
  }
}

// Timeout wrapper helper
function withTimeout<T>(promise: Promise<T>, ms: number, errorMessage = 'Timeout exceeded'): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error(errorMessage)), ms)),
  ]);
}

async function runDiagnostics() {
  // 1. Supabase Check
  try {
    // Set global.WebSocket so Supabase client does not warn/fail in Node.js < 22
    (global as any).WebSocket = ws;
    const supabase = createClient(values.SUPABASE_URL, values.SUPABASE_SERVICE_ROLE_KEY, {
      auth: { persistSession: false },
    });

    // Real client query to check scenarios table
    const supabaseCheck = supabase.from('scenarios').select('id').limit(1);
    const response = (await withTimeout(Promise.resolve(supabaseCheck), 8000, 'Connection to Supabase timed out after 8s')) as any;

    if (response.error) {
      // 42P01 is Postgres error for "relation does not exist" (meaning scenarios table does not exist)
      if (response.error.code === '42P01') {
        logWarn(`Supabase reachable and authenticated, but 'scenarios' table is missing. Did you run migrations?`);
      } else {
        throw response.error;
      }
    } else {
      logOk(`Supabase connected and 'scenarios' table exists`);
    }
  } catch (err: any) {
    exitCode = 1;
    logFail('Supabase connectivity or authentication failed', err);
  }

  // 2. MongoDB Check
  try {
    const maskedMongoUri = maskMongoUri(values.MONGO_URI);
    const mongoClient = new MongoClient(values.MONGO_URI, {
      serverSelectionTimeoutMS: 5000,
    });
    
    await withTimeout(mongoClient.connect(), 6000, 'MongoDB connection timed out after 6s');
    await mongoClient.db().admin().command({ ping: 1 });
    await mongoClient.close();
    logOk(`MongoDB connected successfully (using ${maskedMongoUri})`);
  } catch (err: any) {
    exitCode = 1;
    logFail('MongoDB connection failed', err);
  }

  // 3. LiveKit Check
  try {
    // Normalize ws/wss -> http/https for RoomServiceClient rest connection
    let normalizedLiveKitUrl = values.LIVEKIT_URL;
    if (normalizedLiveKitUrl.startsWith('wss://')) {
      normalizedLiveKitUrl = normalizedLiveKitUrl.replace('wss://', 'https://');
    } else if (normalizedLiveKitUrl.startsWith('ws://')) {
      normalizedLiveKitUrl = normalizedLiveKitUrl.replace('ws://', 'http://');
    }

    const roomService = new RoomServiceClient(
      normalizedLiveKitUrl,
      values.LIVEKIT_API_KEY,
      values.LIVEKIT_API_SECRET
    );
    
    await withTimeout(roomService.listRooms(), 8000, 'LiveKit room listing timed out after 8s');
    logOk('LiveKit authenticated successfully (room list fetched)');
  } catch (err: any) {
    exitCode = 1;
    logFail('LiveKit authentication or reachability failed', err);
  }

  // 4. Upstash Redis Check
  try {
    const sanitizedRedisUrl = sanitizeUrl(values.UPSTASH_REDIS_REST_URL);
    const redis = new Redis({
      url: values.UPSTASH_REDIS_REST_URL,
      token: values.UPSTASH_REDIS_REST_TOKEN,
    });

    const testKey = `doctor_healthcheck_${Date.now()}`;
    
    // Set
    await withTimeout(redis.set(testKey, 'ok', { ex: 10 }), 5000, 'Upstash Redis WRITE operation timed out');
    
    // Get
    const val = await withTimeout(redis.get(testKey), 5000, 'Upstash Redis READ operation timed out');
    if (val !== 'ok') {
      throw new Error(`Integrity error: Expected 'ok', but got '${val}'`);
    }

    // Delete
    await withTimeout(redis.del(testKey), 5000, 'Upstash Redis DELETE operation timed out');
    
    logOk(`Upstash Redis read/write/delete healthcheck succeeded (using ${sanitizedRedisUrl})`);
  } catch (err: any) {
    exitCode = 1;
    logFail('Upstash Redis healthcheck failed', err);
  }

  // Final status report
  console.log('\n-----------------------------------------');
  if (exitCode === 0) {
    console.log(`${colors.ok} ${colors.bold}API environment diagnostics passed successfully!${colors.reset}`);
  } else {
    console.log(`${colors.fail} ${colors.bold}API environment diagnostics failed. See errors above.${colors.reset}`);
  }
  process.exit(exitCode);
}

runDiagnostics();
