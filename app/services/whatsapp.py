import httpx
import time

from app.config import config

CRM_SEND_URL = "https://cudjgjkygqbckypkvdvn.supabase.co/functions/v1/send-whatsapp"
CHANNEL_INFO_URL = "https://cudjgjkygqbckypkvdvn.supabase.co/functions/v1/channel-info"
GRAPH_API_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/messages"

CRM_AUTH = "Bearer 76e8a17614c247eb8f9ab418366bc98f_user_c97"

_headers_crm = {
    "Authorization": CRM_AUTH,
    "Content-Type": "application/json",
}

# Cache de channel-info: { channel_id: (timestamp, data) }
_channel_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # segundos


async def _get_channel_info(channel_id: str) -> dict:
    """Busca info do canal no Supabase com cache de 5 minutos."""
    if not channel_id:
        return {}

    cached = _channel_cache.get(channel_id)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                CHANNEL_INFO_URL,
                params={"channel_id": channel_id},
                headers={"Authorization": CRM_AUTH},
            )
            if r.is_success:
                data = r.json()
                _channel_cache[channel_id] = (time.time(), data)
                print(f"[channel-info] canal={channel_id} proxy={bool(data.get('proxy'))}", flush=True)
                return data
            else:
                print(f"[channel-info] ERRO {r.status_code}: {r.text[:200]}", flush=True)
    except Exception as e:
        print(f"[channel-info] Exceção: {e}", flush=True)

    return {}


def _parse_proxy(proxy_str: str) -> str | None:
    """
    Converte proxy no formato 'host:port:user:pass' para URL httpx.
    Ex: '45.179.90.111:31983:Txi2hu9g:VzEK0FZ2' → 'http://Txi2hu9g:VzEK0FZ2@45.179.90.111:31983'
    Também aceita 'host:port' sem autenticação.
    """
    if not proxy_str:
        return None
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{proxy_str}"
    return None


async def _send_via_graph(
    to: str,
    text: str,
    phone_number_id: str,
    token: str,
    proxy_url: str | None,
) -> dict:
    """Envia mensagem de texto direto pela Graph API do WhatsApp, opcionalmente com proxy."""
    url = GRAPH_API_URL.format(phone_number_id=phone_number_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    client_kwargs = {"timeout": 20}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    async with httpx.AsyncClient(**client_kwargs) as client:
        response = await client.post(url, headers=headers, json=payload)
        if not response.is_success:
            print(f"[graph-api] ERRO {response.status_code}: {response.text[:200]}", flush=True)
        response.raise_for_status()
        return response.json()


async def send_text_message(
    to: str,
    text: str,
    crm_conversation_id: str = "",
    crm_channel_id: str = "",
) -> dict:
    """
    Envia mensagem de texto.
    - Se o canal tiver proxy configurado: usa Graph API diretamente com proxy.
    - Caso contrário: usa a função send-whatsapp do CRM (comportamento anterior).
    """
    # Consulta info do canal para ver se há proxy
    channel_info = await _get_channel_info(crm_channel_id)

    proxy_str = channel_info.get("proxy", "")
    waba_token = channel_info.get("waba_token") or channel_info.get("token", "")
    phone_number_id = (
        channel_info.get("waba_phone_number_id")
        or channel_info.get("phone_number_id", "")
    )

    if proxy_str and waba_token and phone_number_id:
        proxy_url = _parse_proxy(proxy_str)
        print(f"[whatsapp] Enviando via Graph API com proxy para {to}", flush=True)
        return await _send_via_graph(to, text, phone_number_id, waba_token, proxy_url)

    # Sem proxy → CRM padrão
    print(f"[whatsapp] Enviando via CRM para {to}", flush=True)
    payload = {
        "to": to,
        "message": text,
        "channelId": crm_channel_id,
        "conversationId": crm_conversation_id,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(CRM_SEND_URL, headers=_headers_crm, json=payload)
        if not response.is_success:
            print(f"[whatsapp] ERRO {response.status_code}: {response.text[:200]}", flush=True)
        response.raise_for_status()
        return response.json()
