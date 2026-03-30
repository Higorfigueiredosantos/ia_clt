import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

_cache = {"token": None, "expires_at": 0}


def get_token() -> str:
    """Retorna token OAuth V8 Sistema com cache de 55 minutos."""
    if _cache["token"] and time.time() < _cache["expires_at"]:
        return _cache["token"]

    url = os.getenv("V8_AUTH_URL")
    payload = {
        "grant_type": "password",
        "audience": "https://bff.v8sistema.com",
        "scope": "offline_access",
        "client_id": os.getenv("V8_CLIENT_ID"),
        "username": os.getenv("V8_USERNAME"),
        "password": os.getenv("V8_PASSWORD"),
    }
    response = requests.post(url, data=payload)
    response.raise_for_status()

    token = response.json()["access_token"]
    _cache["token"] = token
    _cache["expires_at"] = time.time() + 55 * 60  # 55 minutos

    return token
