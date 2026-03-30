import httpx

CRM_SEND_URL = "https://cudjgjkygqbckypkvdvn.supabase.co/functions/v1/send-whatsapp"
CRM_AUTH = "Bearer 76e8a17614c247eb8f9ab418366bc98f_user_c97"

_headers = {
    "Authorization": CRM_AUTH,
    "Content-Type": "application/json",
}


async def send_text_message(to: str, text: str,
                             crm_conversation_id: str = "",
                             crm_channel_id: str = "") -> dict:
    """Envia mensagem de texto via CRM (Supabase function)."""
    payload = {
        "to": to,
        "message": text,
        "channelId": crm_channel_id,
        "conversationId": crm_conversation_id,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(CRM_SEND_URL, headers=_headers, json=payload)
        if not response.is_success:
            print(f"[whatsapp] ERRO {response.status_code}: {response.text[:200]}", flush=True)
        response.raise_for_status()
        return response.json()
