import os
import asyncio
import ssl
from dotenv import load_dotenv

# Configure SSL certificates for macOS Python environment (copied from voice_agent.py)
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass

# Load workspace .env
agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_dir = os.path.dirname(agent_dir)
dotenv_path = os.path.join(project_dir, ".env")

if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    load_dotenv()

async def check_livekit_connection():
    print("=== LiveKit Cloud Reachability & Credentials Test ===")
    
    url = os.environ.get("LIVEKIT_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    
    if not url or not api_key or not api_secret:
        print("[FAIL] Missing LiveKit environment variables in .env.")
        print(f"  LIVEKIT_URL: {url}")
        print(f"  LIVEKIT_API_KEY: {'[SET]' if api_key else '[MISSING]'}")
        print(f"  LIVEKIT_API_SECRET: {'[SET]' if api_secret else '[MISSING]'}")
        return 1
        
    print(f"Testing URL: {url}")
    print(f"API Key: {api_key}")
    
    # 1. Test HTTPS reachability of the URL
    import urllib.request
    import urllib.error
    
    http_url = url.replace("wss://", "https://").replace("ws://", "http://")
    print(f"\n1. Checking HTTPS server reachability at {http_url} ...")
    
    def _test_request(context=None):
        try:
            response = urllib.request.urlopen(http_url, timeout=6, context=context)
            print(f"  [OK] LiveKit server responded with code {response.getcode()}")
            return True
        except urllib.error.HTTPError as e:
            print(f"  [OK] LiveKit server is online and reachable (responded with HTTP {e.code})")
            return True
        except Exception as e:
            raise e

    try:
        _test_request()
    except ssl.SSLError as e:
        print(f"  [WARNING] Python SSL verification failed locally: {e}")
        print("  Retrying reachability check with unverified SSL context to isolate networking...")
        try:
            unverified_context = ssl._create_unverified_context()
            _test_request(context=unverified_context)
            print("  [OK] Network is fully reachable (TCP/HTTPS handshake succeeded without certificate verification).")
        except Exception as ex:
            print(f"  [FAIL] LiveKit host is NOT reachable. Network error: {ex}")
            return 1
    except Exception as e:
        print(f"  [FAIL] LiveKit host is NOT reachable. Network error: {e}")
        return 1

    # 2. Test LiveKit API Client Credentials by listing active rooms
    print("\n2. Connecting with LiveKit server credentials...")
    try:
        from livekit.api import LiveKitAPI, ListRoomsRequest
        
        # Instantiate standard LiveKit API SDK client
        api = LiveKitAPI(url, api_key, api_secret)
        
        print("  Querying active rooms list from LiveKit Cloud...")
        response = await api.room.list_rooms(ListRoomsRequest())
        rooms = response.rooms
        print(f"  [SUCCESS] Successfully connected to LiveKit Cloud API!")
        print(f"  Active Rooms Count: {len(rooms)}")
        for r in rooms:
            print(f"    - Room name: '{r.name}' (Participants: {r.num_participants})")
            
        return 0
    except Exception as e:
        print(f"  [FAIL] LiveKit API connection failed. Credential or Server Error: {e}")
        return 1

if __name__ == "__main__":
    import sys
    exit_code = asyncio.run(check_livekit_connection())
    sys.exit(exit_code)
