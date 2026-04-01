import time
import requests

_token_cache: str = ""
_token_ts: float = 0.0
_TOKEN_TTL = 55 * 60  # reutiliza por 55 minutos


def get_facta_token() -> str:
    """Retorna token Facta, gerando um novo apenas se o atual expirou."""
    global _token_cache, _token_ts
    if _token_cache and (time.time() - _token_ts) < _TOKEN_TTL:
        return _token_cache
    r = requests.get(
        "https://webservice.facta.com.br/gera-token",
        headers={
            "Authorization": "Basic OTkwMDY6bW54NXg0a2hwY3c4Y2pjMWppZGY=",
        },
        timeout=10,
    )
    r.raise_for_status()
    _token_cache = r.json()["token"]
    _token_ts = time.time()
    return _token_cache
