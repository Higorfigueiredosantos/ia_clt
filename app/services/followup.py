"""
Serviço de follow-up: envia lembretes para clientes que pararam de responder.

Lógica:
- 1º lembrete: 30 minutos após a última mensagem do cliente
- 2º lembrete: 60 minutos após o 1º lembrete (sem resposta)
- Após 2 lembretes: para de incomodar
"""
import asyncio
from datetime import datetime, timezone, timedelta
from app.services.user import get_supabase
from app.services.whatsapp import send_text_message

# Estados que NÃO devem receber follow-up
_ESTADOS_IGNORAR = {
    "GREETING",
    "PROPOSAL_SENT",
    "HUMAN_HANDOFF",
    "ERROR",
    "RUNNING_SIMULATION",
    "WAITING_SCHEDULE_TIME",
    "SCHEDULED",
}

# Mensagens contextuais por estado
_MENSAGENS = {
    "WAITING_SIMULATION_CONFIRM": (
        "Olá! Ainda tem interesse em simular seu crédito CLT? "
        "Posso verificar as opções disponíveis para você agora mesmo! 😊"
    ),
    "WAITING_CPF": (
        "Estou no aguardo do envio do número do seu CPF para que possamos "
        "realizar a consulta. Poderia informar?"
    ),
    "CONFIRM_SIMULATION": (
        "Oi! Ficou com alguma dúvida sobre o valor?"
    ),
    "WAITING_CUSTOM_INSTALLMENT": (
        "Oi! Ainda estou aqui para te ajudar. 😊 "
        "Qual valor de parcela ficaria mais confortável no seu bolso?"
    ),
    "WAITING_CUSTOM_TERM": (
        "Oi! Só falta escolher o prazo para finalizarmos sua simulação. "
        "Qual prefere: 8x, 10x, 12x, 18x, 24x ou 36x? 😊"
    ),
    "WAITING_ADDRESS_CEP": (
        "Para finalizar sua proposta, precisamos do seu CEP. Poderia me informar?"
    ),
    "WAITING_PIX_KEY": (
        "Só falta sua chave PIX para concluirmos a proposta. "
        "Pode me informar? (CPF, celular, e-mail ou chave aleatória)"
    ),
    "FACTA_WAITING_CONSENT": (
        "Já conseguiu clicar no link de autorização? "
        "Me avisa aqui quando concluir para seguirmos! 😊"
    ),
    "FACTA_WAITING_ALT_PHONE": (
        "Ainda aguardo um número de WhatsApp alternativo para enviar o link de autorização. "
        "Tem outro disponível? Me manda no formato (DDD) 9XXXX-XXXX."
    ),
    "FACTA_CONFIRM_SIMULATION": (
        "Oi! Ficou com alguma dúvida sobre o valor?"
    ),
    "FACTA_WAITING_CUSTOM_INSTALLMENT": (
        "Oi! Ainda estou aqui para te ajudar. 😊 "
        "Qual valor de parcela ficaria mais confortável no seu bolso?"
    ),
    "FACTA_WAITING_CUSTOM_TERM": (
        "Oi! Só falta escolher o prazo para finalizarmos sua simulação. "
        "Qual prefere: 12x, 18x, 24x ou 36x? 😊"
    ),
    "FACTA_WAITING_CEP": (
        "Para finalizar sua proposta, precisamos do seu CEP. Poderia me informar?"
    ),
    "FACTA_WAITING_PIX_KEY": (
        "Só falta sua chave PIX para concluirmos a proposta. "
        "Pode me informar? (CPF, celular, e-mail ou chave aleatória)"
    ),
    "FACTA_WAITING_PHONE": (
        "Ainda aguardo um número de celular com DDD para prosseguir. "
        "Pode me informar no formato (DDD) 9XXXX-XXXX?"
    ),
}

# Mensagens do 2º follow-up (diferentes das primeiras)
_MENSAGENS_2 = {
    "WAITING_SIMULATION_CONFIRM": (
        "Oi! Passando para ver se você teve a chance de pensar na simulação do seu crédito CLT. "
        "Quando quiser, é só me chamar! 😊"
    ),
    "WAITING_CPF": (
        "Oi! Ainda não recebi seu CPF. Assim que puder me enviar, dou continuidade à simulação!"
    ),
    "CONFIRM_SIMULATION": (
        "Passando para lembrar que sua simulação ainda está disponível. 💰 "
        "É rápido, seguro e o dinheiro creditado via PIX em até 5 minutos. "
        "Me confirma para possamos prosseguir?"
    ),
    "WAITING_CUSTOM_INSTALLMENT": (
        "Oi! Me conta qual valor de parcela cabe melhor no seu orçamento. "
        "Assim consigo simular certinho para você! 😊"
    ),
    "WAITING_CUSTOM_TERM": (
        "Oi! Ainda aguardo a escolha do prazo. Temos: 8x, 10x, 12x, 18x, 24x ou 36x. Qual prefere?"
    ),
    "WAITING_ADDRESS_CEP": (
        "Oi! Para finalizarmos sua proposta só falta o CEP. Pode me enviar?"
    ),
    "WAITING_PIX_KEY": (
        "Oi! Estamos quase lá! Só falta sua chave PIX para concluir a proposta. 😊"
    ),
    "FACTA_WAITING_CONSENT": (
        "Oi! Conseguiu acessar o link de autorização? Me avisa aqui para darmos continuidade!"
    ),
    "FACTA_WAITING_ALT_PHONE": (
        "Oi! Ainda preciso de um número alternativo de WhatsApp para enviar o link. Tem outro disponível?"
    ),
    "FACTA_CONFIRM_SIMULATION": (
        "Passando para lembrar que sua simulação ainda está disponível. 💰 "
        "É rápido, seguro e o dinheiro creditado via PIX em até 5 minutos. "
        "Me confirma para possamos prosseguir?"
    ),
    "FACTA_WAITING_CUSTOM_INSTALLMENT": (
        "Oi! Me conta qual valor de parcela cabe melhor no seu orçamento. "
        "Assim consigo simular certinho para você! 😊"
    ),
    "FACTA_WAITING_CUSTOM_TERM": (
        "Oi! Ainda aguardo a escolha do prazo. Temos: 12x, 18x, 24x ou 36x. Qual prefere?"
    ),
    "FACTA_WAITING_CEP": (
        "Oi! Para finalizarmos sua proposta só falta o CEP. Pode me enviar?"
    ),
    "FACTA_WAITING_PIX_KEY": (
        "Oi! Estamos quase lá! Só falta sua chave PIX para concluir. 😊"
    ),
    "FACTA_WAITING_PHONE": (
        "Oi! Ainda aguardo seu número de celular com DDD para prosseguir. "
        "Pode me enviar no formato (DDD) 9XXXX-XXXX?"
    ),
}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> datetime:
    """Converte string ISO para datetime aware UTC."""
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _buscar_conversas_para_followup() -> list[dict]:
    """Retorna conversas ativas que precisam de follow-up."""
    sb = get_supabase()
    result = (
        sb.table("conversations")
        .select("id, state, data, user_id, updated_at")
        .execute()
    )
    return result.data or []


def _ultima_mensagem_usuario(conv_id: str) -> datetime:
    """Retorna o timestamp da última mensagem do usuário na conversa."""
    sb = get_supabase()
    result = (
        sb.table("messages")
        .select("created_at")
        .eq("conversation_id", conv_id)
        .eq("role", "user")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return _parse_dt(result.data[0]["created_at"])
    return datetime.min.replace(tzinfo=timezone.utc)


def _buscar_phone_usuario(user_id: str) -> str | None:
    """Busca o telefone do usuário pelo ID."""
    sb = get_supabase()
    result = sb.table("users").select("phone").eq("id", user_id).execute()
    if result.data:
        return result.data[0]["phone"]
    return None


def _atualizar_followup(conv_id: str, data_atual: dict, count: int, followup_1_at: str = None) -> None:
    """Atualiza followup_count e followup_1_at na conversa."""
    sb = get_supabase()
    novo_data = {**data_atual, "followup_count": count}
    if followup_1_at:
        novo_data["followup_1_at"] = followup_1_at
    sb.table("conversations").update({"data": novo_data}).eq("id", conv_id).execute()


_MENSAGENS_AGENDAMENTO = {
    "WAITING_SIMULATION_CONFIRM": "Conforme combinado, retomei o atendimento! Você tem interesse em simular seu crédito CLT?",
    "WAITING_CPF": "Conforme combinado, retomei o atendimento! Para continuar, preciso do seu CPF. Pode me informar?",
    "CONFIRM_SIMULATION": "Conforme combinado, retomei o atendimento! Sua simulação ainda está disponível. Deseja prosseguir?",
    "WAITING_CUSTOM_INSTALLMENT": "Conforme combinado, retomei o atendimento! Qual valor de parcela prefere?",
    "WAITING_CUSTOM_TERM": "Conforme combinado, retomei o atendimento! Qual prazo você prefere para a simulação?",
    "WAITING_ADDRESS_CEP": "Conforme combinado, retomei o atendimento! Para continuar sua proposta, preciso do seu CEP.",
    "WAITING_PIX_KEY": "Conforme combinado, retomei o atendimento! Só falta sua chave PIX para concluirmos.",
    "FACTA_WAITING_CONSENT": "Conforme combinado, retomei o atendimento! Já conseguiu clicar no link de autorização?",
    "FACTA_CONFIRM_SIMULATION": "Conforme combinado, retomei o atendimento! Tenho uma proposta disponível. Deseja prosseguir?",
    "FACTA_WAITING_CUSTOM_INSTALLMENT": "Conforme combinado, retomei o atendimento! Qual valor de parcela prefere?",
    "FACTA_WAITING_CUSTOM_TERM": "Conforme combinado, retomei o atendimento! Qual prazo prefere para a simulação?",
    "FACTA_WAITING_CEP": "Conforme combinado, retomei o atendimento! Para continuar sua proposta, preciso do seu CEP.",
    "FACTA_WAITING_PIX_KEY": "Conforme combinado, retomei o atendimento! Só falta sua chave PIX para concluirmos.",
}


def _atualizar_state_conversa(conv_id: str, new_state: str, data_atual: dict) -> None:
    sb = get_supabase()
    novo_data = {k: v for k, v in data_atual.items() if k not in ("scheduled_at", "scheduled_resume_state")}
    sb.table("conversations").update({"state": new_state, "data": novo_data}).eq("id", conv_id).execute()


async def verificar_e_enviar_followups():
    """Verifica todas as conversas ativas e envia follow-ups quando necessário."""
    agora = _agora()
    conversas = _buscar_conversas_para_followup()

    for conv in conversas:
        state = conv.get("state", "GREETING")
        data = conv.get("data") or {}

        # ── Agendamentos ──────────────────────────────────────────────────────
        if state == "SCHEDULED":
            scheduled_at_str = data.get("scheduled_at", "")
            if not scheduled_at_str:
                continue
            scheduled_at = _parse_dt(scheduled_at_str)
            if agora < scheduled_at:
                continue
            resume_state = data.get("scheduled_resume_state", "WAITING_SIMULATION_CONFIRM")
            mensagem = _MENSAGENS_AGENDAMENTO.get(resume_state,
                "Conforme combinado, retomei o atendimento! Como posso ajudar?")
            phone = _buscar_phone_usuario(conv["user_id"])
            if not phone:
                continue
            crm_conversation_id = data.get("crm_conversation_id", "")
            crm_channel_id = data.get("crm_channel_id", "")
            try:
                await send_text_message(phone, mensagem,
                                        crm_conversation_id=crm_conversation_id,
                                        crm_channel_id=crm_channel_id)
                _atualizar_state_conversa(conv["id"], resume_state, data)
                print(f"[followup] Agendamento disparado para {phone} -> {resume_state}", flush=True)
            except Exception as e:
                print(f"[followup] Erro ao disparar agendamento para {phone}: {e}", flush=True)
            continue

        # IA desativada para esse contato → nunca enviar follow-up
        if data.get("ia_desativada"):
            continue

        if state in _ESTADOS_IGNORAR:
            continue
        if state not in _MENSAGENS:
            continue

        followup_count = int(data.get("followup_count", 0))

        if followup_count >= 2:
            continue

        user_id = conv["user_id"]
        conv_id = conv["id"]

        # Pega timestamp da última mensagem do usuário
        ultima_msg = _ultima_mensagem_usuario(conv_id)
        if ultima_msg == datetime.min.replace(tzinfo=timezone.utc):
            continue  # Sem mensagens ainda

        tempo_desde_ultima = agora - ultima_msg

        deve_enviar = False

        if followup_count == 0:
            # 1º follow-up: 30 minutos após última mensagem do cliente
            if tempo_desde_ultima >= timedelta(minutes=30):
                deve_enviar = True

        elif followup_count == 1:
            # 2º follow-up: 60 minutos após o 1º lembrete
            followup_1_at = _parse_dt(data.get("followup_1_at", ""))
            if followup_1_at != datetime.min.replace(tzinfo=timezone.utc):
                tempo_desde_followup1 = agora - followup_1_at
                if tempo_desde_followup1 >= timedelta(hours=1):
                    deve_enviar = True

        if not deve_enviar:
            continue

        phone = _buscar_phone_usuario(user_id)
        if not phone:
            continue

        if followup_count == 0:
            mensagem = _MENSAGENS.get(state, "")
        else:
            mensagem = _MENSAGENS_2.get(state, _MENSAGENS.get(state, ""))
        if not mensagem:
            continue

        crm_conversation_id = data.get("crm_conversation_id", "")
        crm_channel_id = data.get("crm_channel_id", "")

        try:
            await send_text_message(phone, mensagem,
                                    crm_conversation_id=crm_conversation_id,
                                    crm_channel_id=crm_channel_id)
            print(f"[followup] Enviado para {phone} estado={state} count={followup_count+1}", flush=True)

            novo_count = followup_count + 1
            followup_1_at_novo = agora.isoformat() if followup_count == 0 else None
            _atualizar_followup(conv_id, data, novo_count, followup_1_at_novo)

        except Exception as e:
            print(f"[followup] Erro ao enviar para {phone}: {e}", flush=True)


async def iniciar_servico_followup():
    """Loop infinito que verifica follow-ups a cada 5 minutos."""
    print("[followup] Serviço iniciado — verificando a cada 5 minutos", flush=True)
    while True:
        await asyncio.sleep(5 * 60)  # aguarda 5 minutos antes de verificar
        try:
            await verificar_e_enviar_followups()
        except Exception as e:
            print(f"[followup] Erro no ciclo: {e}", flush=True)
