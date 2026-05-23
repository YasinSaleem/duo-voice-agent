import os
from dotenv import load_dotenv

# 1. Centralized Dotenv Environment Loading
# Check for a local .env file in the agent folder first
agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(agent_dir, '.env')
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

# 2. Centralized macOS SSL Certificate Environment Overrides
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass
