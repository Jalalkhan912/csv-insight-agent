import os
from dotenv import load_dotenv, find_dotenv


def load_env():
    load_dotenv(find_dotenv(usecwd=True), override=True)


def get_llm_api_key():
    """API key for the LLM provider. GEMINI_API_KEY wins; OPENAI_API_KEY is a fallback."""
    load_env()
    return os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
