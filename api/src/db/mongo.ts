import { MongoClient, Db } from 'mongodb';
import 'dotenv/config';

const mongoUri = process.env.MONGO_URI;
if (!mongoUri) {
  throw new Error('MONGO_URI environment variable is missing.');
}
const uri: string = mongoUri;

let clientInstance: MongoClient | null = null;
let dbInstance: Db | null = null;
let connectPromise: Promise<MongoClient> | null = null;

export async function connectMongo(): Promise<MongoClient> {
  if (clientInstance) {
    return clientInstance;
  }

  // Promise lock pattern: concurrent callers await the same initialization promise
  if (!connectPromise) {
    const maskedUri = uri.replace(/\/\/([^:]+):([^@]+)@/, '//***:***@');
    console.log(`[MongoDB] Initializing lazy client connection to ${maskedUri}`);

    connectPromise = (async () => {
      const client = new MongoClient(uri, {
        maxPoolSize: 10,
        minPoolSize: 2
      });

      // Log transient and other errors for observability only
      // Rely on MongoDB driver internal connection pool recovery
      client.on('error', (err) => {
        console.error('[MongoDB] Connection lifecycle error:', err);
      });

      // Teardown singleton ONLY on terminal close events
      client.on('close', () => {
        console.warn('[MongoDB] Connection closed, tearing down singleton.');
        dbInstance = null;
        clientInstance = null;
        connectPromise = null;
      });

      await client.connect();
      clientInstance = client;
      return client;
    })();
  }

  return connectPromise;
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
