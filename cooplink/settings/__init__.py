import os
import environ
from pathlib import Path

# Setup paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read .env to populate os.environ before choosing settings
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

# Load settings based on DJANGO_ENV
env_type = os.environ.get("DJANGO_ENV", "dev")

if env_type == "prod":
    from .prod import *
else:
    from .dev import *
