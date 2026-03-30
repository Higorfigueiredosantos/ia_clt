import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # V8 Sistema
    V8_AUTH_URL: str = os.getenv("V8_AUTH_URL", "")
    V8_CLIENT_ID: str = os.getenv("V8_CLIENT_ID", "")
    V8_USERNAME: str = os.getenv("V8_USERNAME", "")
    V8_PASSWORD: str = os.getenv("V8_PASSWORD", "")

    # WhatsApp
    WHATSAPP_TOKEN: str = os.getenv("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_BUSINESS_ID: str = os.getenv("WHATSAPP_BUSINESS_ID", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "clt_verify_token_2026")

    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # App
    PORT: int = int(os.getenv("PORT", "8000"))

    # Config V8 para simulação CLT
    V8_CONFIG_ID: str = "fbbb3a06-05ca-4567-9a92-ce78cb4db796"


config = Config()
