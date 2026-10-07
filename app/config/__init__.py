"""
Unified configuration loader for the AIDLC Platform Agents.

Loads environment properties in a fixed precedence (highest wins):

    OS env  >  .env  >  config.json  >  Settings field defaults

  1. ``.env`` is loaded first with ``override=False`` so OS/container env always wins.
  2. A single ``config.json`` (non-secret local + dev defaults) is pushed into
     ``os.environ`` — ONLY for keys not already set, so OS env / .env always win.
     There are NO per-env config files; uat/prod override via OS env at deploy.
  3. Settings is imported last, after the environment is fully populated.

SECRETS NEVER live in config.json / committed .env — they are Vault-managed. The
JSON holds endpoints, Vault *names*, flags, and the model registry.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("config-init")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(levelname)s]\t%(asctime)s\t%(message)s"))
    logger.addHandler(_handler)

# 1. Load .env (local/internal) — override=False so OS/container env wins.
#    app/.env is the per-machine override file (e.g. PG_CONNECTION_MODE=local); it is read first by an
#    explicit path so the result does not depend on the working directory or on how Python was started.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
load_dotenv(override=False)

CURRENT_ENV = os.environ.get("APP_ENV", os.environ.get("ENV", "dev")).lower()
CONFIG_DIR = Path(__file__).parent


def _read_config_file(config_file: Path) -> dict:
    """Read a JSON config file and return its contents as a dict (no side effects)."""
    if not config_file.exists():
        logger.info(f"Config file {config_file.name} not found — skipping.")
        return {}
    try:
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        logger.info(f"Loaded configuration from {config_file.name}")
        return config
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Error loading config from {config_file}: {exc}")
        return {}


def load_env_config() -> dict:
    """Load the single ``config.json`` (local + dev defaults) without clobbering OS/dotenv."""
    logger.info(f"Loading configuration for environment: {CURRENT_ENV}")
    os_keys = set(os.environ.keys())
    config = _read_config_file(CONFIG_DIR / "config.json")
    for key, value in config.items():
        if key not in os_keys:  # never override OS/dotenv
            os.environ[key] = json.dumps(value) if isinstance(value, dict | list) else str(value)
    return config


# CRITICAL: populate the environment BEFORE importing Settings.
env_config = load_env_config()

# Imported AFTER the environment is populated so Settings reads the merged config.
from .settings import Settings, get_settings  # noqa: E402,F401

__all__ = ["Settings", "get_settings", "env_config", "CURRENT_ENV"]
