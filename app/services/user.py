from supabase import create_client, Client
from app.config import config

_client: Client = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


def get_or_create_user(phone: str, name: str = None) -> dict:
    """
    Busca usuário pelo telefone. Se não existir, cria com nome e telefone.
    Retorna o registro do usuário.
    """
    sb = get_supabase()

    result = sb.table("users").select("*").eq("phone", phone).execute()

    if result.data:
        return result.data[0]

    new_user = sb.table("users").insert({"phone": phone, "name": name}).execute()
    return new_user.data[0]


def update_user(user_id: str, fields: dict) -> dict:
    """Atualiza campos do usuário.
    Se incluir CPF, remove esse CPF de outros usuários antes (evita constraint violation)."""
    sb = get_supabase()
    if "cpf" in fields and fields["cpf"]:
        # Remove CPF de outros usuários que possam ter o mesmo valor (testes, etc.)
        sb.table("users").update({"cpf": None}).eq("cpf", fields["cpf"]).neq("id", user_id).execute()
    result = sb.table("users").update(fields).eq("id", user_id).execute()
    return result.data[0]
