import core.bootstrap
import os
import sys
import re
import time
import urllib.request
import json
import asyncio
import ssl

# Terminal coloring
class Colors:
    OK = '\033[92m[OK]\033[0m'
    WARN = '\033[93m[WARN]\033[0m'
    FAIL = '\033[91m[FAIL]\033[0m'
    INFO = '\033[94m[INFO]\033[0m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

# Check for verbose flag
verbose = "--verbose" in sys.argv

print(f"{Colors.INFO} {Colors.BOLD}Starting Agent Environment Diagnostics...{Colors.RESET}\n")

# Format patterns
url_regex = re.compile(r'^https?://[^\s$.?#].[^\s]*$', re.IGNORECASE)
mongo_regex = re.compile(r'^mongodb(\+srv)?://.+', re.IGNORECASE)
livekit_regex = re.compile(r'^(wss?|https?)://[^\s$.?#].[^\s]*$', re.IGNORECASE)

def mask_mongo_uri(uri: str) -> str:
    match = re.match(r'^(mongodb(?:\+srv)?://)([^/\s?]+)', uri, re.IGNORECASE)
    if not match:
        return 'mongodb://***@unknown...'
    scheme = match.group(1)
    authority = match.group(2)
    
    at_index = authority.rfind('@')
    host = authority[at_index + 1:] if at_index != -1 else authority
    
    cluster_match = re.match(r'^(cluster)', host, re.IGNORECASE)
    if cluster_match:
        masked_host = cluster_match.group(1) + '...'
    elif len(host) > 10:
        masked_host = host[:7] + '...'
    else:
        masked_host = host
        
    return f"{scheme}***@{masked_host}"

def sanitize_url(url: str) -> str:
    # First, remove any credentials (e.g. username:password@)
    clean_url = re.sub(r'^([a-zA-Z+.-]+://)(?:[^@]+)@(.*)$', r'\1\2', url, flags=re.IGNORECASE)
    
    # Match protocol and host
    match = re.match(r'^([a-zA-Z+.-]+://)([^/]+)', clean_url, re.IGNORECASE)
    if not match:
        return url
        
    protocol = match.group(1)
    host = match.group(2)
    
    if host.lower().endswith('.upstash.io'):
        return f"{protocol}***.upstash.io"
        
    parts = host.split('.')
    if len(parts) > 2:
        return f"{protocol}***.{'.'.join(parts[1:])}"
        
    return f"{protocol}***"

def mask_secrets(text: str) -> str:
    masked = text
    # Mask MongoDB URI credentials (e.g. mongodb+srv://user:pass@host)
    masked = re.sub(r'(mongodb(?:\+srv)?://[^:]+:)([^@]+)(@)', r'\1***\3', masked, flags=re.IGNORECASE)
    
    # Mask any direct mentions of the sensitive env var values
    sensitive_keys = [
        "GROQ_API_KEY", "DEEPGRAM_API_KEY", "LIVEKIT_API_SECRET", 
        "MONGO_URI", "UPSTASH_REDIS_REST_TOKEN", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"
    ]
    for key in sensitive_keys:
        secret = os.getenv(key)
        if secret and len(secret) > 4:
            masked = masked.replace(secret, "***")
    return masked

def create_verified_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

def get_ca_file():
    try:
        import certifi
        return certifi.where()
    except Exception:
        return None

expected_vars = [
    {"key": "GROQ_API_KEY", "required": True},
    {"key": "DEEPGRAM_API_KEY", "required": True},
    {"key": "LIVEKIT_URL", "required": True, "regex": livekit_regex, "error": "Must be a valid ws/wss or http/https URL"},
    {"key": "LIVEKIT_API_KEY", "required": True},
    {"key": "LIVEKIT_API_SECRET", "required": True},
    {"key": "MONGO_URI", "required": True, "regex": mongo_regex, "error": "Must be a valid MongoDB URI (starting with mongodb:// or mongodb+srv://)"},
    {"key": "UPSTASH_REDIS_REST_URL", "required": True, "regex": url_regex, "error": "Must be a valid HTTP/HTTPS URL"},
    {"key": "UPSTASH_REDIS_REST_TOKEN", "required": True},
    {"key": "SUPABASE_URL", "required": True, "regex": url_regex, "error": "Must be a valid HTTP/HTTPS URL"},
    {"key": "SUPABASE_SERVICE_ROLE_KEY", "required": True},
    {"key": "SUPABASE_ANON_KEY", "required": True}
]

validation_failed = False
values = {}

print(f"{Colors.BOLD}--- Environment Variables Check ---{Colors.RESET}")
for var in expected_vars:
    key = var["key"]
    val = os.getenv(key)
    
    if not val:
        print(f"{Colors.FAIL} {key} is missing")
        validation_failed = True
        continue

    if key == "MONGO_URI" and val.startswith("MONGO_URI="):
        print(f"{Colors.FAIL} {key} appears to include the key name inside the value. Use `MONGO_URI=mongodb://...` or `MONGO_URI=mongodb+srv://...`")
        validation_failed = True
        continue
        
    if "regex" in var and not var["regex"].match(val):
        print(f"{Colors.FAIL} {key} has invalid format. {var['error']}")
        validation_failed = True
        continue
        
    values[key] = val
    print(f"{Colors.OK} {key} is set and format is valid")

if validation_failed:
    print(f"\n{Colors.FAIL} {Colors.BOLD}Environment validation failed. Please fix your .env file before running diagnostics.{Colors.RESET}")
    sys.exit(1)


print(f"\n{Colors.BOLD}--- External Services Connectivity Checks ---{Colors.RESET}")

exit_code = 0

def log_ok(msg):
    print(f"{Colors.OK} {msg}")

def log_warn(msg):
    print(f"{Colors.WARN} {msg}")

def log_fail(msg, err=None):
    print(f"{Colors.FAIL} {msg}")
    if err:
        if verbose:
            import traceback
            tb_str = traceback.format_exc()
            print(mask_secrets(tb_str))
        else:
            print(f"  Reason: {mask_secrets(str(err))}")

async def run_diagnostics():
    global exit_code
    
    # 1. MongoDB Check
    try:
        masked_mongo_uri = mask_mongo_uri(values["MONGO_URI"])
        from pymongo import MongoClient
        client = MongoClient(
            values["MONGO_URI"],
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsCAFile=get_ca_file(),
        )
        # Verify connectivity
        client.admin.command("ping")
        client.close()
        log_ok(f"MongoDB connected successfully (using {masked_mongo_uri})")
    except Exception as e:
        exit_code = 1
        log_fail("MongoDB connection failed", e)

    # 2. Upstash Redis Check
    try:
        sanitized_redis_url = sanitize_url(values["UPSTASH_REDIS_REST_URL"])
        from upstash_redis import Redis
        redis = Redis(url=values["UPSTASH_REDIS_REST_URL"], token=values["UPSTASH_REDIS_REST_TOKEN"])
        test_key = f"doctor_healthcheck_{int(time.time())}"
        
        # Read/Write/Delete healthcheck
        redis.set(test_key, "ok", ex=10)
        val = redis.get(test_key)
        if val != "ok":
            raise Exception(f"Integrity check failed: expected 'ok', got '{val}'")
        redis.delete(test_key)
        
        log_ok(f"Upstash Redis read/write/delete healthcheck succeeded (using {sanitized_redis_url})")
    except Exception as e:
        exit_code = 1
        log_fail("Upstash Redis healthcheck failed", e)

    # 3. LiveKit Check
    client = None
    session = None
    try:
        # Normalize ws/wss -> http/https for RoomServiceClient rest connection
        normalized_livekit_url = values["LIVEKIT_URL"]
        if normalized_livekit_url.startswith("wss://"):
            normalized_livekit_url = normalized_livekit_url.replace("wss://", "https://")
        elif normalized_livekit_url.startswith("ws://"):
            normalized_livekit_url = normalized_livekit_url.replace("ws://", "http://")

        from livekit import api
        import aiohttp
        ssl_context = create_verified_ssl_context()
        session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context))
        # LiveKitAPI is the container for services in the livekit-api package
        client = api.LiveKitAPI(
            normalized_livekit_url,
            values["LIVEKIT_API_KEY"],
            values["LIVEKIT_API_SECRET"],
            session=session
        )
        
        # list_rooms returns an awaitable
        await asyncio.wait_for(client.room.list_rooms(api.ListRoomsRequest()), timeout=8.0)
        log_ok("LiveKit authenticated successfully (room list fetched)")
    except Exception as e:
        exit_code = 1
        log_fail("LiveKit authentication or reachability failed", e)
    finally:
        if client is not None:
            await client.aclose()
        if session is not None:
            await session.close()

    # 4. Groq Check
    try:
        from groq import Groq
        client = Groq(api_key=values["GROQ_API_KEY"])
        # Validate authentication using lightweight models metadata call
        client.models.list()
        log_ok("Groq authenticated successfully (models list fetched)")
    except Exception as e:
        exit_code = 1
        log_fail("Groq authentication failed", e)

    # 5. Deepgram Check
    try:
        # Validate authentication using lightweight projects endpoint via urllib
        req = urllib.request.Request(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {values['DEEPGRAM_API_KEY']}"}
        )
        
        # Use certifi-backed CA roots when available to avoid local OpenSSL trust-store issues.
        context = create_verified_ssl_context()
        with urllib.request.urlopen(req, timeout=5.0, context=context) as response:
            res_data = json.loads(response.read().decode())
            if "projects" not in res_data:
                raise Exception("Response format invalid: expected 'projects' key")
                
        log_ok("Deepgram authenticated successfully (projects list fetched)")
    except Exception as e:
        exit_code = 1
        log_fail("Deepgram authentication failed", e)

    # 6. Supabase Check
    try:
        from supabase import create_client
        supabase = create_client(values["SUPABASE_URL"], values["SUPABASE_SERVICE_ROLE_KEY"])
        
        # Check scenarios table
        scenarios_res = supabase.table("scenarios").select("id").limit(1).execute()
        log_ok("Supabase connected and 'scenarios' table is accessible")
        
        # Check memories table
        memories_res = supabase.table("memories").select("id").limit(1).execute()
        log_ok("Supabase connected and 'memories' table is accessible")
    except Exception as e:
        exit_code = 1
        log_fail("Supabase connectivity or authentication failed", e)

    # Final summary report
    print("\n-----------------------------------------")
    if exit_code == 0:
        print(f"{Colors.OK} {Colors.BOLD}Agent environment diagnostics passed successfully!{Colors.reset if hasattr(Colors, 'reset') else Colors.RESET}")
    else:
        print(f"{Colors.FAIL} {Colors.BOLD}Agent environment diagnostics failed. See errors above.{Colors.reset if hasattr(Colors, 'reset') else Colors.RESET}")
        
    sys.exit(exit_code)

if __name__ == "__main__":
    asyncio.run(run_diagnostics())
