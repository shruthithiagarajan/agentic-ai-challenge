"""Tiny stdlib .env loader so DESK_MODEL / ANTHROPIC_API_KEY can live in a local .env file."""
import os
from pathlib import Path


def load_dotenv(path=None, env=os.environ):
    """Load KEY=VALUE lines from .env into env. Existing environment variables win. Returns keys set."""
    p = Path(path) if path else Path.cwd() / ".env"
    if not p.is_file():
        return []
    loaded = []
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and val and key not in env:
            env[key] = val
            loaded.append(key)
    return loaded
