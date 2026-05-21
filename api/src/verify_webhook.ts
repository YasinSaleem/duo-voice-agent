import express from 'express';
import { supabaseAdmin } from './db/supabase';
import { AccessToken } from 'livekit-server-sdk';
import crypto from 'crypto';
import http from 'http';
import livekitRouter from './routes/livekit';

// Ensure environment variables are loaded
import 'dotenv/config';

// 1. Setup temporary test app
const testApp = express();
testApp.use(express.json({
  type: ['application/json', 'application/webhook+json'],
  verify: (req: any, _res, buf) => {
    req.rawBody = buf;
  }
}));
testApp.use('/internal/livekit', livekitRouter);

async function runWebhookTest() {
  console.log('[verify_webhook] Starting LiveKit webhook integration tests...');
  let sessionId: string | undefined;
  let userId: string | undefined;
  let tempUserCreated = false;

  // 1. Find a valid scenario in Supabase
  console.log('[verify_webhook] Querying active scenario in Supabase...');
  const { data: scenarios, error: scenarioErr } = await supabaseAdmin
    .from('scenarios')
    .select('id')
    .limit(1);

  if (scenarioErr || !scenarios || scenarios.length === 0) {
    throw new Error('No scenarios found in Supabase to run tests.');
  }
  const scenarioId = scenarios[0].id;
  console.log(`[verify_webhook] Using scenario ID: ${scenarioId}`);

  // 2. Create a temporary active session for testing
  console.log('[verify_webhook] Querying standard session or user in database...');
  const { data: users, error: userErr } = await supabaseAdmin
    .from('sessions')
    .select('user_id')
    .limit(1);

  if (!userErr && users && users.length > 0) {
    userId = users[0].user_id;
  } else {
    // If no sessions exist, query auth users
    const { data: authUsers, error: authErr } = await supabaseAdmin.auth.admin.listUsers();
    if (!authErr && authUsers && authUsers.users.length > 0) {
      userId = authUsers.users[0].id;
    } else {
      console.log('[verify_webhook] No auth users found. Creating a temporary auth user...');
      const { data: newUser, error: createAuthErr } = await supabaseAdmin.auth.admin.createUser({
        email: `test-webhook-${Date.now()}@example.com`,
        password: 'TemporaryPassword123!',
        email_confirm: true
      });
      if (createAuthErr || !newUser || !newUser.user) {
        throw new Error(`Failed to create temporary auth user: ${createAuthErr?.message}`);
      }
      userId = newUser.user.id;
      tempUserCreated = true;
      console.log(`[verify_webhook] Created temporary auth user ID: ${userId}`);
    }
  }
  console.log(`[verify_webhook] Using user ID: ${userId}`);

  // Insert temporary session
  console.log('[verify_webhook] Creating temporary test session...');
  const { data: session, error: createSessionErr } = await supabaseAdmin
    .from('sessions')
    .insert({
      user_id: userId,
      scenario_id: scenarioId,
      status: 'active'
    })
    .select()
    .single();

  if (createSessionErr || !session) {
    throw new Error(`Failed to create test session: ${createSessionErr?.message}`);
  }
  sessionId = session.id;
  console.log(`[verify_webhook] Created test session ID: ${sessionId}`);

  // 3. Start local HTTP server
  const testPort = 3005;
  const server = http.createServer(testApp);
  await new Promise<void>((resolve) => server.listen(testPort, resolve));
  console.log(`[verify_webhook] Local test server started on port ${testPort}`);

  // 4. Configure env for mock python agent spawning
  process.env.PYTHON_BINARY = 'echo'; // Mock python executable to print args and exit immediately

  try {
    // 5. Construct webhook event payload
    const payload = {
      event: 'room_started',
      room: {
        name: sessionId
      }
    };
    const bodyStr = JSON.stringify(payload);

    // 6. Generate authentic signature
    console.log('[verify_webhook] Generating cryptographic LiveKit signature...');
    const sha256 = crypto.createHash('sha256').update(bodyStr).digest('base64');
    
    const at = new AccessToken(
      process.env.LIVEKIT_API_KEY!,
      process.env.LIVEKIT_API_SECRET!
    );
    at.sha256 = sha256;
    const signature = await at.toJwt();

    // 7. Make HTTP POST request to test webhook endpoint
    console.log('[verify_webhook] Dispatching webhook request to /internal/livekit/webhook...');
    const responseData = await new Promise<any>((resolve, reject) => {
      const request = http.request(
        {
          hostname: 'localhost',
          port: testPort,
          path: '/internal/livekit/webhook',
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': signature
          }
        },
        (res) => {
          let chunks = '';
          res.on('data', (chunk) => {
            chunks += chunk;
          });
          res.on('end', () => {
            resolve({
              statusCode: res.statusCode,
              body: JSON.parse(chunks)
            });
          });
        }
      );

      request.on('error', reject);
      request.write(bodyStr);
      request.end();
    });

    console.log(`[verify_webhook] Response status: ${responseData.statusCode}`);
    console.log('[verify_webhook] Response body:', responseData.body);

    // 8. Assertions
    if (responseData.statusCode !== 200) {
      throw new Error(`Expected status 200, got ${responseData.statusCode}`);
    }
    if (responseData.body.ok !== true) {
      throw new Error(`Expected body { ok: true }, got ${JSON.stringify(responseData.body)}`);
    }
    console.log('[verify_webhook] Webhook signature and db session retrieval validated successfully.');

  } finally {
    // 9. Cleanup database
    if (sessionId) {
      console.log('[verify_webhook] Cleaning up temporary test session...');
      const { error: deleteErr } = await supabaseAdmin
        .from('sessions')
        .delete()
        .eq('id', sessionId);

      if (deleteErr) {
        console.warn(`[verify_webhook] Warning: failed to delete test session ${sessionId}:`, deleteErr.message);
      } else {
        console.log('[verify_webhook] Session cleaned up successfully from database.');
      }
    }

    if (tempUserCreated && userId) {
      console.log(`[verify_webhook] Cleaning up temporary auth user ${userId}...`);
      const { error: deleteUserErr } = await supabaseAdmin.auth.admin.deleteUser(userId);
      if (deleteUserErr) {
        console.warn(`[verify_webhook] Warning: failed to delete temporary auth user:`, deleteUserErr.message);
      } else {
        console.log('[verify_webhook] Temporary auth user cleaned up successfully.');
      }
    }

    // Close server
    await new Promise<void>((resolve) => server.close(() => resolve()));
    console.log('[verify_webhook] Test server stopped.');
  }

  console.log('\n[verify_webhook] === SUCCESS: LiveKit Webhook integration verified successfully! ===\n');
}

runWebhookTest().catch((err) => {
  console.error('[verify_webhook] FAILURE:', err.message);
  process.exit(1);
});
