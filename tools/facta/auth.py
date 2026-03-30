import os
import requests


def get_facta_token() -> str:
    """Busca o token Facta da tabela facta_token no Supabase."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    r = requests.get(
        f"{url}/rest/v1/facta_token",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
        },
        params={"select": "token", "order": "updated_at.desc", "limit": "1"},
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows or not rows[0].get("token"):
        raise RuntimeError("Token Facta nao encontrado na tabela facta_token")
    return rows[0]["token"]
