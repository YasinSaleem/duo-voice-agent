const { RoomServiceClient } = require('livekit-server-sdk');
require('dotenv').config();

const apiKey = process.env.LIVEKIT_API_KEY;
const apiSecret = process.env.LIVEKIT_API_SECRET;
const livekitUrl = process.env.LIVEKIT_URL;

async function check() {
  console.log("Initializing RoomServiceClient with:", {
    livekitUrl,
    apiKey: apiKey ? 'Present' : 'Missing',
    apiSecret: apiSecret ? 'Present' : 'Missing'
  });

  try {
    const svc = new RoomServiceClient(livekitUrl, apiKey, apiSecret);
    console.log("Fetching active rooms list from LiveKit Cloud...");
    const rooms = await svc.listRooms();
    console.log("Success! Active Rooms:", rooms);
  } catch (error) {
    console.error("Failed to connect to LiveKit or fetch rooms:", error);
  }
}

check();
