import os
from pathlib import Path

import environ

# Setup paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read .env to populate os.environ before choosing settings
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

# Load base settings first (defines all defaults)
from .base import *  # noqa: E402, F401, F403

# Then override with environment-specific settings
env_type = os.environ.get("DJANGO_ENV", "dev")

if env_type == "prod":
    from .prod import *  # noqa: E402, F401, F403
else:
    from .dev import *  # noqa: E402, F401, F403
