"""
Inicia o servidor FastAPI + ngrok juntos.
Execute: python start.py
"""
import os
import re
import time
import uvicorn
import threading
import requests as _requests
from dotenv import load_dotenv
from pyngrok import ngrok, conf

load_dotenv()

PORT = int(os.getenv("PORT", "8000"))
AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "")


def _print_url(public_url):
    msg = (
        f"\n{'='*60}\n"
        f"  SERVIDOR ONLINE\n"
        f"{'='*60}\n"
        f"  URL local:   http://localhost:{PORT}\n"
        f"  URL pública: {public_url}\n\n"
        f"  Configure no Meta for Developers:\n"
        f"  Webhook URL: {public_url}/webhook\n"
        f"  Verify Token: {os.getenv('WHATSAPP_VERIFY_TOKEN', 'clt_verify_token_2026')}\n"
        f"{'='*60}\n"
    )
    print(msg, flush=True)


def _get_url_from_ngrok_api() -> str | None:
    """Tenta obter URL de túnel ativo via API local do ngrok."""
    for port in range(4040, 4046):
        try:
            r = _requests.get(f"http://localhost:{port}/api/tunnels", timeout=2)
            tunnels = r.json().get("tunnels", [])
            for t in tunnels:
                url = t.get("public_url", "")
                if url.startswith("https://"):
                    return url
        except Exception:
            pass
    return None


def start_ngrok():
    if AUTHTOKEN:
        conf.get_default().auth_token = AUTHTOKEN

    # 1. Verifica se já existe túnel ativo via API local
    existing_url = _get_url_from_ngrok_api()
    if existing_url:
        _print_url(existing_url)
        return

    # 2. Tenta criar novo túnel
    try:
        tunnel = ngrok.connect(PORT, "http")
        _print_url(tunnel.public_url)
        return
    except Exception as e:
        err_str = str(e)
        # ERR_NGROK_334: endpoint já online — extrai URL do erro
        match = re.search(r"https://[\w\-]+\.ngrok[\-\w\.]+\.dev", err_str)
        if match:
            _print_url(match.group(0))
            return
        # Tenta API local novamente após tentativa de conexão
        time.sleep(2)
        existing_url = _get_url_from_ngrok_api()
        if existing_url:
            _print_url(existing_url)
            return
        print(f"[ngrok] Não foi possível obter URL pública. Servidor: http://localhost:{PORT}")


if __name__ == "__main__":
    # Inicia ngrok em thread separada
    thread = threading.Thread(target=start_ngrok, daemon=True)
    thread.start()
    thread.join(timeout=8)

    # Inicia servidor
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=False)
