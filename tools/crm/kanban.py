import httpx

_URL = "https://cudjgjkygqbckypkvdvn.supabase.co/functions/v1/manage-contacts"
_HEADERS = {
    "Authorization": "Bearer 76e8a17614c247eb8f9ab418366bc98f_user_c97",
    "Content-Type": "application/json",
}


async def atualizar_kanban(contact_id: str, stage: str) -> None:
    """Atualiza o estágio do contato no Kanban do CRM."""
    if not contact_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.put(
                f"{_URL}?contact_id={contact_id}",
                headers=_HEADERS,
                json={"stage": stage},
            )
            if response.is_success:
                print(f"[kanban] {contact_id} -> {stage!r}", flush=True)
            else:
                print(f"[kanban] ERRO {response.status_code}: {response.text[:200]}", flush=True)
    except Exception as e:
        print(f"[kanban] Erro ao atualizar: {e}", flush=True)
