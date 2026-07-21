"""Central config. Reads from environment / .env — nothing hardcoded, no secrets in the repo."""
import os
import pathlib

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent

# ── LLM (OpenAI-compatible; provider-agnostic) ──────────────────────────────
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-opus-4.1")

# ── Storage ─────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "finance.db"))


def llm_configured() -> bool:
    return bool(LLM_API_KEY)
