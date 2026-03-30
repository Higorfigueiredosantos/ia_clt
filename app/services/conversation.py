from app.services.user import get_supabase


def get_or_create_conversation(user_id: str) -> dict:
    """Busca conversa ativa do usuário. Se não existir, cria com estado inicial."""
    sb = get_supabase()

    result = sb.table("conversations").select("*").eq("user_id", user_id).execute()

    if result.data:
        return result.data[0]

    new_conv = sb.table("conversations").insert({
        "user_id": user_id,
        "state": "GREETING",
        "data": {},
    }).execute()
    return new_conv.data[0]


def update_conversation(conv_id: str, state: str, data_update: dict = None) -> dict:
    """
    Atualiza estado e faz merge parcial do JSONB data.
    Usa || para não sobrescrever dados acumulados anteriores.
    """
    sb = get_supabase()

    fields = {"state": state}

    if data_update:
        # Busca data atual e faz merge manual
        current = sb.table("conversations").select("data").eq("id", conv_id).execute()
        current_data = current.data[0]["data"] if current.data else {}
        merged_data = {**current_data, **data_update}
        fields["data"] = merged_data

    result = sb.table("conversations").update(fields).eq("id", conv_id).execute()
    return result.data[0]


def reset_conversation(conv_id: str) -> dict:
    """Reinicia a conversa: zera estado, dados e histórico de mensagens."""
    sb = get_supabase()
    sb.table("messages").delete().eq("conversation_id", conv_id).execute()
    result = sb.table("conversations").update({
        "state": "GREETING",
        "data": {},
    }).eq("id", conv_id).execute()
    return result.data[0]


def save_message(conv_id: str, role: str, content: str) -> dict:
    """Salva uma mensagem no histórico. role: 'user' ou 'assistant'."""
    sb = get_supabase()
    result = sb.table("messages").insert({
        "conversation_id": conv_id,
        "role": role,
        "content": content,
    }).execute()
    return result.data[0]


def get_messages(conv_id: str, limit: int = 20) -> list[dict]:
    """Retorna as últimas N mensagens da conversa ordenadas por data."""
    sb = get_supabase()
    result = (
        sb.table("messages")
        .select("role, content, created_at")
        .eq("conversation_id", conv_id)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data or []
