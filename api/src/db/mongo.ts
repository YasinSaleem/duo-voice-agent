import { MongoClient, Db } from 'mongodb';
import 'dotenv/config';

const mongoUri = process.env.MONGO_URI;
if (!mongoUri) {
  throw new Error('MONGO_URI environment variable is missing.');
}
const uri: string = mongoUri;

let clientInstance: MongoClient | null = null;
let dbInstance: Db | null = null;

export async function connectMongo(): Promise<MongoClient> {
  if (clientInstance) {
    return clientInstance;
  }

  const maskedUri = uri.replace(/\/\/([^:]+):([^@]+)@/, '//***:***@');
  console.log(`[MongoDB] Initializing lazy client connection to ${maskedUri}`);

  clientInstance = new MongoClient(uri, {
    maxPoolSize: 10,
    minPoolSize: 2
  });

  // Log transient and other errors for observability only
  // Rely on MongoDB driver internal connection pool recovery
  clientInstance.on('error', (err) => {
    console.error('[MongoDB] Connection lifecycle error:', err);
  });

  // Teardown singleton ONLY on terminal close events
  clientInstance.on('close', () => {
    console.warn('[MongoDB] Connection closed, tearing down singleton.');
    dbInstance = null;
    clientInstance = null;
  });

  await clientInstance.connect();
  return clientInstance;
}

export async function getDb(): Promise<Db> {
  if (dbInstance) return dbInstance;
  const client = await connectMongo();
  dbInstance = client.db('language_tutor');
  return dbInstance;
}

export async function getTurnsCollection() {
  const db = await getDb();
  return db.collection('turns');
}
