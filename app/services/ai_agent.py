import re
import asyncio
import io
import httpx
from datetime import datetime, timezone, timedelta
from openai import OpenAI
from tools.crm.kanban import atualizar_kanban
from app.config import config
from app.workflows.state_machine import State
from app.workflows.prompts import SYSTEM_PROMPT, ERROR_MESSAGE
from app.services.user import get_or_create_user, update_user
from app.services.conversation import (
    get_or_create_conversation, update_conversation,
    save_message, get_messages, reset_conversation,
)
from app.services.whatsapp import send_text_message
from tools.v8.dados_cliente import buscar_dados_cliente
from tools.v8.consulta import executar_fluxo_consulta, buscar_dados_consulta, ConsultaEmAnaliseError, ConsultaRejeitadaError
from tools.v8.simulacao import pre_calcular_installments, executar_simulacao
from tools.v8.proposta import gerar_proposta
from tools.facta.auth import get_facta_token
from tools.facta.dados_cliente import buscar_dados_cliente_facta
from tools.facta.consulta import verificar_autorizacao, enviar_termo
from tools.facta.simulacao import buscar_simulacoes, gravar_simulacao, MatriculaRequeridaError
from tools.facta.proposta import (
    buscar_codigo_cidade,
    detectar_tipo_pix as facta_detectar_tipo_pix,
    gravar_dados_pessoais,
    digitar_proposta,
)

openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

# Lock por telefone para evitar race condition com mensagens simultâneas
_phone_locks: dict[str, asyncio.Lock] = {}

# Debounce: acumula mensagens rápidas e processa tudo de uma vez
_DEBOUNCE_SECONDS = 11
_debounce_tasks:    dict[str, asyncio.Task] = {}
_debounce_texts:    dict[str, list[str]]    = {}
_debounce_meta:     dict[str, dict]         = {}

# Follow-up pós-proposta
_followup_tasks: dict[str, list[asyncio.Task]] = {}


def _brl(valor: float) -> str:
    """Formata valor monetário no padrão brasileiro: R$ 1.153,84"""
    return f"R$ {valor:_.2f}".replace("_", "X").replace(".", ",").replace("X", ".")


async def _desativar_automacao(contact_id: str, phone: str = "") -> None:
    """Desativa a IA via Supabase toggle-automation e cancela follow-ups pendentes."""
    if phone:
        _cancelar_followups(phone)
    if not contact_id:
        return
    try:
        url = f"{config.SUPABASE_URL}/functions/v1/toggle-automation"
        async with httpx.AsyncClient() as client:
            r = await client.post(
                url,
                params={"contact_id": contact_id},
                headers={
                    "Authorization": f"Bearer {config.SUPABASE_KEY}",
                    "Content-Type": "application/json",
                },
                json={"automation_active": False},
                timeout=10,
            )
        print(f"[toggle-automation] contact_id={contact_id} status={r.status_code}", flush=True)
    except Exception as e:
        print(f"[toggle-automation] Erro: {e}", flush=True)


def _cancelar_followups(phone: str) -> None:
    """Cancela follow-ups agendados para o telefone."""
    for task in _followup_tasks.get(phone, []):
        task.cancel()
    _followup_tasks[phone] = []


async def _followup_worker(phone: str, delay_seconds: int, mensagem: str,
                            crm_conversation_id: str, crm_channel_id: str,
                            conv_id: str) -> None:
    """Espera delay_seconds e envia mensagem se cliente ainda estiver em PROPOSAL_SENT."""
    try:
        await asyncio.sleep(delay_seconds)
        from app.services.conversation import get_or_create_conversation
        from app.services.user import get_or_create_user
        # Verifica estado atual via DB
        user = get_or_create_user(phone, "")
        conv = get_or_create_conversation(user["id"])
        if conv.get("state") == State.PROPOSAL_SENT.value:
            await send_text_message(phone, mensagem,
                                    crm_conversation_id=crm_conversation_id,
                                    crm_channel_id=crm_channel_id)
            print(f"[followup] Enviado para {phone}: {mensagem[:60]!r}", flush=True)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[followup] Erro: {e}", flush=True)


def _agendar_followups(phone: str, crm_conversation_id: str,
                        crm_channel_id: str, conv_id: str) -> None:
    """Agenda follow-up de 30min e 1h após envio da proposta."""
    _cancelar_followups(phone)
    msg1 = "Verifiquei que ainda não finalizou a proposta, está com alguma dificuldade em que possa ajudar?"
    msg2 = "É só clicar no link da proposta acima e realizar o passo a passo. Após a formalização em até 5 minutos será creditado!"
    t1 = asyncio.create_task(_followup_worker(phone, 30 * 60, msg1, crm_conversation_id, crm_channel_id, conv_id))
    t2 = asyncio.create_task(_followup_worker(phone, 60 * 60, msg2, crm_conversation_id, crm_channel_id, conv_id))
    _followup_tasks[phone] = [t1, t2]
    print(f"[followup] Agendado 30min e 1h para {phone}", flush=True)


def _detectar_tipo_pix_inteligente(pix_key: str, cpf: str = "", phone: str = "") -> str:
    """Detecta tipo da chave PIX cruzando com CPF e telefone do cliente.
    Retorna: '1'=CPF, '2'=Telefone, '3'=Email, '4'=Aleatória
    """
    if "@" in pix_key:
        return "3"

    digits = re.sub(r"\D", "", pix_key)
    cpf_digits = re.sub(r"\D", "", cpf or "")
    phone_digits = re.sub(r"\D", "", phone or "")
    # Remove DDI 55 do telefone salvo para comparar só DDD+número
    if phone_digits.startswith("55") and len(phone_digits) > 11:
        phone_digits = phone_digits[2:]

    # Correspondência exata com CPF salvo
    if cpf_digits and digits == cpf_digits:
        return "1"

    # Correspondência com telefone salvo (com ou sem 9 na frente, com ou sem DDD)
    if phone_digits and len(digits) >= 8:
        # Compara sufixos: últimos 8 dígitos
        if digits[-8:] == phone_digits[-8:]:
            return "2"

    # Sem correspondência: usa heurística por tamanho
    if len(digits) == 11:
        # 11 dígitos: se começa com 0 a 9 e parece celular (terceiro dígito = 9), é telefone
        if len(digits) == 11 and digits[2] == "9":
            return "2"
        return "1"  # assume CPF
    if len(digits) >= 10:
        return "2"  # 10 dígitos = telefone sem o nono dígito

    return "4"  # chave aleatória


async def _transcrever_audio(media_url: str) -> str:
    """Baixa áudio do Storage e transcreve via Whisper. Retorna o texto."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(media_url)
        resp.raise_for_status()
        audio_bytes = resp.content
    arquivo = io.BytesIO(audio_bytes)
    arquivo.name = "audio.ogg"
    transcricao = await asyncio.to_thread(
        lambda: openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=arquivo,
            language="pt",
        )
    )
    return transcricao.text.strip()


async def _descrever_imagem(media_url: str) -> str:
    """Envia imagem para GPT-4o Vision e retorna descrição/conteúdo relevante."""
    resposta = await asyncio.to_thread(
        lambda: openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Você é um assistente de crédito CLT. O cliente enviou esta imagem. "
                            "Descreva de forma objetiva o que está na imagem — se for um documento "
                            "(RG, CPF, comprovante), extraia os dados visíveis. "
                            "Se for outra coisa, descreva resumidamente."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": media_url}},
                ],
            }],
            max_tokens=500,
        )
    )
    return resposta.choices[0].message.content.strip()

# Mapeamento de novo estado → estágio no Kanban do CRM
_KANBAN_STAGES: dict[str, str] = {
    "WAITING_SIMULATION_CONFIRM":       "Lead Novo",
    "WAITING_CPF":                      "Aguardando CPF",
    "CONFIRM_SIMULATION":               "Simulacao enviada",
    "FACTA_CONFIRM_SIMULATION":         "Simulacao enviada",
    "WAITING_CUSTOM_INSTALLMENT":       "Negociacao",
    "WAITING_CUSTOM_TERM":              "Negociacao",
    "FACTA_WAITING_CUSTOM_INSTALLMENT": "Negociacao",
    "FACTA_WAITING_CUSTOM_TERM":        "Negociacao",
    "FACTA_WAITING_CONSENT":            "Autorizar Termo Facta",
    "FACTA_WAITING_ALT_PHONE":          "Autorizar Termo Facta",
    "PROPOSAL_SENT":                    "Proposta gerada",
    "WAITING_SCHEDULE_TIME":            "Agendamento",
    "SCHEDULED":                        "Agendamento",
}

# Estados onde NÃO deve detectar "ocupado"
_ESTADOS_SEM_AGENDAMENTO = {
    State.GREETING, State.PROPOSAL_SENT, State.HUMAN_HANDOFF,
    State.RUNNING_SIMULATION, State.SCHEDULED, State.WAITING_SCHEDULE_TIME,
    State.ERROR,
}

_FRASES_OCUPADO = (
    "mais tarde", "mais tarde", "ocupado", "ocupada", "no serviço", "no trabalho",
    "trabalhando", "trabalhando", "agora nao", "agora não", "nao posso agora",
    "não posso agora", "posso falar depois", "me chame", "me liga depois",
    "to no servico", "tô no serviço", "to trabalhando", "tô trabalhando",
    "estou no servico", "estou no serviço", "depois eu", "falo depois",
    "chama depois", "pode chamar depois", "tô ocupado", "to ocupado",
)

_BR_TZ = timezone(timedelta(hours=-3))


def _detectar_ocupado(text: str) -> bool:
    t = text.lower()
    return any(f in t for f in _FRASES_OCUPADO)


def _calcular_horario_agendamento(text: str) -> str:
    """Retorna ISO timestamp (com tz) do horário agendado baseado no texto ou horário atual."""
    agora_br = datetime.now(_BR_TZ)
    t = text.lower()

    # 1º: tempo relativo — "daqui X minutos", "em X minutos", "X horas"
    m_min = re.search(r'(?:daqui|em|mais)?\s*(\d+)\s*min(?:uto)?s?', t)
    if m_min:
        agendado = agora_br + timedelta(minutes=int(m_min.group(1)))
        agendado = agendado.replace(second=0, microsecond=0)
        return agendado.isoformat()

    m_hr = re.search(r'(?:daqui|em|mais)?\s*(\d+)\s*h(?:ora)?s?', t)
    if m_hr:
        agendado = agora_br + timedelta(hours=int(m_hr.group(1)))
        agendado = agendado.replace(second=0, microsecond=0)
        return agendado.isoformat()

    # 2º: horário fixo — "15:30", "15h30", "15h", "às 15"
    m = re.search(r'\b(\d{1,2})(?:[:h](\d{2}))?\s*h?\b', t)
    if m:
        hora = int(m.group(1))
        minuto = int(m.group(2) or 0)
        if 0 <= hora <= 23:
            agendado = agora_br.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            if agendado <= agora_br:
                agendado += timedelta(days=1)
            return agendado.isoformat()

    # 3º: sem horário específico — usa regra pelo período do dia
    hora_atual = agora_br.hour + agora_br.minute / 60
    if 6 <= hora_atual < 11.5:
        agendado = agora_br.replace(hour=12, minute=0, second=0, microsecond=0)
    elif 12 <= hora_atual < 17:
        agendado = agora_br.replace(hour=17, minute=15, second=0, microsecond=0)
    else:
        amanha = agora_br + timedelta(days=1)
        agendado = amanha.replace(hour=9, minute=0, second=0, microsecond=0)
    return agendado.isoformat()

GREETING_MESSAGE = (
    "Olá! Meu nome é Cassia da Cunha, faço parte do time de especialistas.\n\n"
    "Você tem interesse em simular seu beneficio CLT?"
)


def _extrair_cpf(text: str) -> str:
    """Extrai sequência de exatamente 11 dígitos consecutivos do texto (CPF)."""
    # Aceita formatos: 12345678901, 123.456.789-01, 123 456 789 01
    m = re.search(r'\b(\d{3})[.\s-]?(\d{3})[.\s-]?(\d{3})[.\s-]?(\d{2})\b', text)
    if m:
        return "".join(m.groups())
    return ""


def _vai_rodar_simulacao(state: State, data: dict, text: str, user: dict) -> bool:
    """Retorna True se a mensagem vai disparar uma simulação (operação lenta)."""
    if state == State.RUNNING_SIMULATION:
        return True
    if state == State.WAITING_CPF:
        return len(_extrair_cpf(text)) == 11
    if state == State.WAITING_SIMULATION_CONFIRM:
        return False  # sempre vai pedir CPF antes de simular
    if state == State.FACTA_WAITING_CONSENT:
        t = text.lower().strip()
        palavras = set(re.findall(r"[a-záéíóúâêîôûãõàèìòùç]+", t))
        return bool(palavras & {"sim", "ok", "autorizei", "ja", "já", "autorizado", "aceito", "feito", "pronto"})
    return False


async def handle_message(phone: str, name: str, text: str, message_id: str,
                         crm_conversation_id: str = "", crm_channel_id: str = "",
                         crm_contact_id: str = "",
                         media_url: str = "", media_type: str = ""):
    """Ponto de entrada principal. Transcreve mídia e aplica debounce antes de processar."""
    # Converte áudio/imagem em texto imediatamente (antes do debounce)
    if media_type == "audio" and media_url:
        try:
            text = await _transcrever_audio(media_url)
            print(f"[handle] Áudio transcrito: {text[:80]!r}", flush=True)
        except Exception as e:
            print(f"[handle] Erro ao transcrever áudio: {e}", flush=True)
            text = ""
    elif media_type == "image" and media_url:
        try:
            descricao = await _descrever_imagem(media_url)
            print(f"[handle] Imagem descrita: {descricao[:80]!r}", flush=True)
            text = f"{text} {descricao}".strip() if text and text not in ("📷 Imagem",) else descricao
        except Exception as e:
            print(f"[handle] Erro ao descrever imagem: {e}", flush=True)
            text = text or ""

    if not text:
        print(f"[handle] Mensagem sem conteúdo, ignorando", flush=True)
        return

    # Acumula texto no buffer de debounce
    if phone not in _debounce_texts:
        _debounce_texts[phone] = []
    _debounce_texts[phone].append(text)

    # Sempre atualiza o meta com os dados mais recentes
    _debounce_meta[phone] = {
        "name": name,
        "message_id": message_id,
        "crm_conversation_id": crm_conversation_id,
        "crm_channel_id": crm_channel_id,
        "crm_contact_id": crm_contact_id,
    }

    # Cancela timer anterior e reinicia
    task = _debounce_tasks.get(phone)
    if task and not task.done():
        task.cancel()
    _debounce_tasks[phone] = asyncio.create_task(_debounce_flush(phone))
    print(f"[debounce] Timer reiniciado para {phone} ({len(_debounce_texts[phone])} msg(s) acumulada(s))", flush=True)


async def _debounce_flush(phone: str):
    """Aguarda o debounce e processa todas as mensagens acumuladas de uma vez."""
    await asyncio.sleep(_DEBOUNCE_SECONDS)

    texts = _debounce_texts.pop(phone, [])
    meta  = _debounce_meta.pop(phone, {})
    if not texts or not meta:
        return

    combined = "\n".join(texts)
    print(f"[debounce] Processando {len(texts)} msg(s) para {phone}: {combined[:60]!r}", flush=True)

    if phone not in _phone_locks:
        _phone_locks[phone] = asyncio.Lock()
    async with _phone_locks[phone]:
        await _handle_message_locked(
            phone, meta["name"], combined, meta["message_id"],
            meta["crm_conversation_id"], meta["crm_channel_id"], meta["crm_contact_id"],
        )


async def _handle_message_locked(phone: str, name: str, text: str, message_id: str,
                                  crm_conversation_id: str = "", crm_channel_id: str = "",
                                  crm_contact_id: str = ""):
    """Processamento da mensagem com lock por telefone já adquirido."""
    print(f"[handle] INICIO phone={phone} msg_id={message_id} crm_conv={crm_conversation_id} text={text[:40].encode('ascii','replace').decode('ascii')!r}", flush=True)
    try:
        user = get_or_create_user(phone, name)
        conv = get_or_create_conversation(user["id"])
        try:
            state = State(conv["state"])
        except ValueError:
            state = State.GREETING
        data = conv["data"] or {}

        print(f"[handle] phone={phone} user_id={user['id']} conv_id={conv['id']} state={state.value} cpf_stored={user.get('cpf')}", flush=True)

        # Persiste IDs do CRM para uso no envio de mensagens e kanban
        if crm_conversation_id:
            data["crm_conversation_id"] = crm_conversation_id
        if crm_channel_id:
            data["crm_channel_id"] = crm_channel_id
        if crm_contact_id:
            data["crm_contact_id"] = crm_contact_id
        else:
            crm_contact_id = data.get("crm_contact_id", "")

        save_message(conv["id"], "user", text)
        history = get_messages(conv["id"], limit=20)

        # Zera contador de follow-up ao receber nova mensagem do cliente
        if data.get("followup_count", 0) > 0:
            data["followup_count"] = 0
            data["followup_1_at"] = None

        # Cancela follow-ups agendados quando cliente manda qualquer mensagem
        _cancelar_followups(phone)

        # Detecta opt-out por texto → desativa IA e follow-ups
        _RE_OPTOUT = re.compile(
            r'\b(n[aã]o\s*quero|parar?\s*mensagens?|para\s*de\s*mandar|n[aã]o\s*tenho\s*interesse|'
            r'n[aã]o\s*quero\s*mais|cancela[r]?\s*mensagens?|bloquear?\s*contato|remover?\s*da?\s*lista|'
            r'n[aã]o\s*me\s*contacte|deixa\s*pra\s*l[aá]|desisti|n[aã]o\s*preciso)\b',
            re.I
        )
        if _RE_OPTOUT.search(text):
            data["ia_desativada"] = True
            asyncio.create_task(_desativar_automacao(crm_contact_id, phone=phone))

        # Remove flag ia_desativada caso IA tenha sido reativada (mensagem normal)
        elif data.get("ia_desativada"):
            data.pop("ia_desativada", None)

        # Detecta número errado → pede desculpa, encerra e desativa IA
        _RE_NUMERO_ERRADO = re.compile(
            r'\b(n[uú]mero\s*errado|engano|n[aã]o\s*[eé]\s*(essa|esta|minha|meu)\s*(pessoa|contato|n[uú]mero)?|'
            r'n[aã]o\s*conhe[cç]o|quem\s*[eé]\s*(isso|essa|voc[eê])|'
            r'esse\s*n[uú]mero\s*n[aã]o\s*[eé]|n[aã]o\s*sou\s*eu|'
            r'acho\s*que\s*[eé]\s*engano|caiu\s*no\s*engano|n[uú]mero\s*trocado)\b',
            re.I
        )
        if _RE_NUMERO_ERRADO.search(text):
            _msg_encerramento = "Peço desculpas pelo inconveniente! Encerramos por aqui. Tenha um ótimo dia! 😊"
            save_message(conv["id"], "assistant", _msg_encerramento)
            update_conversation(conv["id"], State.HUMAN_HANDOFF.value, {**data, "ia_desativada": True})
            await send_text_message(phone, _msg_encerramento,
                                    crm_conversation_id=crm_conversation_id,
                                    crm_channel_id=crm_channel_id)
            await _desativar_automacao(crm_contact_id, phone=phone)
            print(f"[handle] Numero errado detectado para {phone}, encerrando.", flush=True)
            return

        # Envia feedback imediato antes de operações lentas (simulação ~30-60s)
        if _vai_rodar_simulacao(state, data, text, user):
            await send_text_message(phone, "Certo, um instante que irei simular!",
                                    crm_conversation_id=crm_conversation_id,
                                    crm_channel_id=crm_channel_id)

        reply, new_state, data_update = await _process(state, data, text, user, history, conv["id"])

        # reply=None significa mensagem enfileirada que deve ser silenciosamente ignorada
        if reply is None:
            print(f"[ai_agent] IGNORANDO mensagem enfileirada phone={phone} state={state.value}", flush=True)
            return

        reply_preview = reply[:60].encode("ascii", "replace").decode("ascii")
        print(f"[ai_agent] state={state.value} -> {new_state.value} reply={reply_preview!r}", flush=True)

        # Garante que os IDs do CRM sejam salvos junto com o update
        if crm_conversation_id:
            data_update["crm_conversation_id"] = crm_conversation_id
        if crm_channel_id:
            data_update["crm_channel_id"] = crm_channel_id
        if crm_contact_id:
            data_update["crm_contact_id"] = crm_contact_id

        update_conversation(conv["id"], new_state.value, data_update)
        save_message(conv["id"], "assistant", reply)

        # Atualiza Kanban se o estado mudou para um estágio mapeado
        stage = _KANBAN_STAGES.get(new_state.value)
        if stage and new_state != state:
            await atualizar_kanban(crm_contact_id, stage)

        print(f"[handle] ENVIANDO phone={phone} crm_conv={crm_conversation_id} reply={reply[:60].encode('ascii','replace').decode('ascii')!r}", flush=True)
        await send_text_message(phone, reply,
                                crm_conversation_id=crm_conversation_id,
                                crm_channel_id=crm_channel_id)

        # Agenda follow-ups quando a proposta acaba de ser enviada
        if new_state == State.PROPOSAL_SENT and state != State.PROPOSAL_SENT:
            _agendar_followups(phone, crm_conversation_id, crm_channel_id, conv["id"])

        # Desativa IA quando cliente confirma que finalizou a proposta
        if state == State.PROPOSAL_SENT and new_state == State.HUMAN_HANDOFF:
            await _desativar_automacao(crm_contact_id, phone=phone)

        print(f"[handle] FIM OK phone={phone}", flush=True)

    except Exception as e:
        import traceback
        print(f"[ai_agent] Erro: {e}", flush=True)
        try:
            tb = traceback.format_exc()
            print(tb.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
        try:
            await send_text_message(phone, ERROR_MESSAGE,
                                    crm_conversation_id=crm_conversation_id,
                                    crm_channel_id=crm_channel_id)
        except Exception as e2:
            print(f"[ai_agent] Erro ao enviar ERROR_MESSAGE: {e2}", flush=True)


_FRASES_ERRO = (
    "não consegui verificar",
    "problema técnico",
    "tente novamente",
    "instabilidade",
    "dificuldades para processar",
    "enfrentando dificuldades",
)

def _filtrar_historico(history: list[dict]) -> list[dict]:
    """Remove mensagens de erro do histórico para não confundir o GPT."""
    limpo = []
    for msg in history:
        conteudo = msg.get("content", "").lower()
        if msg["role"] == "assistant" and any(f in conteudo for f in _FRASES_ERRO):
            continue  # descarta mensagem de erro
        limpo.append(msg)
    # Mantém apenas as últimas 10 mensagens para evitar ruído de histórico antigo
    return limpo[-10:]


def _ask_gpt(state: State, data: dict, user: dict, history: list[dict], context: str = "") -> str:
    history = _filtrar_historico(history)
    conversation_data = {
        "margem_disponivel": data.get("margem_disponivel"),
        "simulation_realizada": bool(data.get("simulation_id")),
        "proposta_em_andamento": bool(data.get("consult_id")),
        "nome_cliente": user.get("name"),
        "employer": data.get("employer_name"),
    }
    system = SYSTEM_PROMPT.format(
        state=state.value,
        conversation_data=conversation_data,
    )
    messages = [{"role": "system", "content": system}]

    # Injeta contexto como instrução de sistema logo antes da última mensagem do usuário
    # para que o GPT não se perca no histórico anterior
    if context and history:
        for msg in history[:-1]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "system", "content": f"[INSTRUÇÃO]: {context}"})
        messages.append({"role": history[-1]["role"], "content": history[-1]["content"]})
    else:
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        if len(messages) == 1:
            messages.append({"role": "user", "content": "Olá"})

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=512,
        messages=messages,
    )
    return response.choices[0].message.content


async def _process(state: State, data: dict, text: str, user: dict, history: list[dict], conv_id: str = "") -> tuple:

    # ── NORMALIZAÇÃO DE EMOJI ─────────────────────────────────────────────────
    # 👍 e variantes de tom de pele → "sim"
    if re.search(r'👍[\U0001F3FB-\U0001F3FF]?', text):
        text = re.sub(r'👍[\U0001F3FB-\U0001F3FF]?', 'sim', text).strip()

    # ── COMANDO DE RESET (para testes) ────────────────────────────────────────
    if text.strip().lower() == "reseti":
        if conv_id:
            reset_conversation(conv_id)
            update_user(user["id"], {"cpf": None, "birth_date": None, "gender": None})
        return "🔄 Conversa reiniciada! Histórico e dados apagados.", State.GREETING, {}

    # ── COMANDO DE TESTE FACTA (para testes internos) ─────────────────────────
    m_facta = re.match(r'^!facta\s+(\d{11})$', text.strip())
    if m_facta:
        cpf_teste = m_facta.group(1)
        update_user(user["id"], {"cpf": cpf_teste})
        user["cpf"] = cpf_teste
        return await _iniciar_fluxo_facta(user, {**data, "cpf": cpf_teste})

    # ── DETECÇÃO DE OCUPADO (qualquer estado, exceto os bloqueados) ───────────
    if state not in _ESTADOS_SEM_AGENDAMENTO and _detectar_ocupado(text):
        return (
            "Compreendo! 😊\n\n"
            "Por gentileza, me informe um horário que fique bom para voltarmos com o atendimento?"
        ), State.WAITING_SCHEDULE_TIME, {"scheduled_resume_state": state.value}

    # ── SCHEDULED (cliente enviou mensagem antes do horário agendado) ─────────
    if state == State.SCHEDULED:
        resume_state_str = data.get("scheduled_resume_state", "WAITING_SIMULATION_CONFIRM")
        try:
            resumed = State(resume_state_str)
        except ValueError:
            resumed = State.WAITING_SIMULATION_CONFIRM
        clean_data = {k: v for k, v in data.items() if k not in ("scheduled_at", "scheduled_resume_state")}
        return await _process(resumed, clean_data, text, user, history, conv_id)

    # ── WAITING_SCHEDULE_TIME ─────────────────────────────────────────────────
    if state == State.WAITING_SCHEDULE_TIME:
        scheduled_at = _calcular_horario_agendamento(text)
        resume_state = data.get("scheduled_resume_state", "WAITING_SIMULATION_CONFIRM")
        # Formata horário para exibir ao cliente
        try:
            dt = datetime.fromisoformat(scheduled_at)
            hora_fmt = dt.strftime("%H:%M")
        except Exception:
            hora_fmt = "no horário combinado"
        return (
            f"Perfeito! Retornarei o atendimento às {hora_fmt}. Até logo! 😊"
        ), State.SCHEDULED, {
            "scheduled_at": scheduled_at,
            "scheduled_resume_state": resume_state,
        }

    # ── SAUDAÇÃO EM MEIO AO ATENDIMENTO ──────────────────────────────────────
    # Se o cliente mandar uma saudação enquanto já está em um estado de espera,
    # responde com cordialidade e retoma a etapa atual.
    _RE_SAUDACAO = re.compile(
        r'^(oi|ol[aá]|e a[ií]|eae|opa|hey|hei|bom\s*dia|boa\s*tarde|boa\s*noite|'
        r'oi\s*boa\s*tarde|oi\s*bom\s*dia|oi\s*boa\s*noite|tudo\s*bem|tudo\s*bom|'
        r'como\s*vai|como\s*voc[eê]\s*est[aá])[\?\!\.\,\s]*$',
        re.I
    )
    _RETOMADA = {
        State.WAITING_CPF:                    "Olá, tudo bem? 😊 Ficou faltando o envio do seu CPF para darmos continuidade. Poderia informar?",
        State.WAITING_SIMULATION_CONFIRM:     "Olá, tudo bem? 😊 Ainda posso simular seu crédito CLT agora. Quer que eu simule?",
        State.WAITING_CUSTOM_INSTALLMENT:     "Olá, tudo bem? 😊 Ainda aguardo o valor de parcela que prefere para finalizar sua simulação.",
        State.WAITING_CUSTOM_TERM:            "Olá, tudo bem? 😊 Ainda aguardo a escolha do prazo: 12x, 18x, 24x ou 36x.",
        State.WAITING_ADDRESS_CEP:            "Olá, tudo bem? 😊 Ficou faltando o seu CEP para concluirmos a proposta. Pode me informar?",
        State.WAITING_PIX_KEY:                "Olá, tudo bem? 😊 Só falta sua chave PIX para concluirmos a proposta. Pode me informar?",
        State.CONFIRM_SIMULATION:             "Olá, tudo bem? 😊 Sua simulação ainda está disponível aqui. Deseja prosseguir com a proposta?",
        State.FACTA_CONFIRM_SIMULATION:       "Olá, tudo bem? 😊 Sua simulação ainda está disponível aqui. Deseja prosseguir com a proposta?",
        State.FACTA_WAITING_CONSENT:          "Olá, tudo bem? 😊 Ainda aguardo a autorização pelo link que enviamos. Já conseguiu clicar?",
        State.FACTA_WAITING_ALT_PHONE:        "Olá, tudo bem? 😊 Ainda aguardo um número de WhatsApp alternativo para enviar o link de autorização.",
        State.FACTA_WAITING_CUSTOM_INSTALLMENT: "Olá, tudo bem? 😊 Ainda aguardo o valor de parcela que prefere para finalizar sua simulação.",
        State.FACTA_WAITING_CUSTOM_TERM:      "Olá, tudo bem? 😊 Ainda aguardo a escolha do prazo: 12x, 18x, 24x ou 36x.",
        State.FACTA_WAITING_CEP:              "Olá, tudo bem? 😊 Ficou faltando o seu CEP para concluirmos a proposta. Pode me informar?",
        State.FACTA_WAITING_PIX_KEY:          "Olá, tudo bem? 😊 Só falta sua chave PIX para concluirmos a proposta. Pode me informar?",
        State.FACTA_WAITING_PHONE:            "Olá, tudo bem? 😊 Ainda aguardo um número de celular com DDD para prosseguir.",
    }
    if _RE_SAUDACAO.match(text.strip()) and state in _RETOMADA:
        return _RETOMADA[state], state, {}

    # ── GREETING ──────────────────────────────────────────────────────────────
    if state == State.GREETING:
        # Botão "Ver detalhes" → pula saudação e vai direto ao CPF
        if text.strip().lower() == "ver detalhes":
            return "Por gentileza informe o numero do seu CPF, para que possa simular.", State.WAITING_CPF, {}
        # Mensagens que já indicam interesse em simular → pula recepção e vai direto ao CPF
        _t_greeting = text.lower().strip()
        _palavras_interesse = set(re.findall(r"[a-záéíóúâêîôûãõàèìòùç]+", _t_greeting))
        _tem_interesse = bool(_palavras_interesse & {"interesse", "interessado", "interessada", "simular", "simulacao", "simulação", "quero", "queria", "gostaria"})
        _tem_credito = bool(_palavras_interesse & {"credito", "crédito", "clt", "emprestimo", "empréstimo"})
        if _tem_interesse and _tem_credito:
            return "Por gentileza informe o numero do seu CPF, para que possa simular.", State.WAITING_CPF, {}
        return GREETING_MESSAGE, State.WAITING_SIMULATION_CONFIRM, {}

    # ── WAITING_SIMULATION_CONFIRM ────────────────────────────────────────────
    if state == State.WAITING_SIMULATION_CONFIRM:
        t = text.lower().strip()
        palavras = set(re.findall(r"[a-záéíóúâêîôûãõàèìòùç]+", t))
        if t == "1" or bool(palavras & {"sim", "ok", "quero", "pode", "vamos", "claro", "yes"}):
            return "Por gentileza informe o numero do seu CPF, para que possa simular.", State.WAITING_CPF, {}
        if t == "2" or bool(palavras & {"nao", "não", "agora", "depois"}):
            return "Tudo bem! Se precisar, é só me chamar. Tenha um ótimo dia! 😊", State.GREETING, {}
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "Responda a dúvida ou comentário do cliente usando o FAQ. Depois pergunte se ele quer simular o crédito CLT agora.")
        return reply, State.WAITING_SIMULATION_CONFIRM, {}

    # ── WAITING_CPF ──────────────────────────────────────────────────────────
    if state == State.WAITING_CPF:
        cpf = _extrair_cpf(text)
        if len(cpf) == 11:
            print(f"[handle] WAITING_CPF phone={user.get('phone')} user_id={user['id']} cpf_novo={cpf}", flush=True)
            update_user(user["id"], {"cpf": cpf})
            user["cpf"] = cpf
            return await _rodar_simulacao_padrao(user, {**data, "cpf": cpf})
        # Não é CPF → trata como pergunta/comentário e retoma pedindo CPF
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "Responda a dúvida ou comentário do cliente usando o FAQ. Depois peça o CPF (11 dígitos) para continuar a simulação.")
        return reply, State.WAITING_CPF, {}

    # ── RUNNING_SIMULATION ────────────────────────────────────────────────────
    if state == State.RUNNING_SIMULATION:
        return await _rodar_simulacao_padrao(user, data)

    # ── CONFIRM_SIMULATION ────────────────────────────────────────────────────
    if state == State.CONFIRM_SIMULATION:
        t = text.lower().strip()
        margem = data.get("margem_disponivel", 0)

        # Detecta mensagem enfileirada: se o usuário enviou antes da simulação chegar
        # (timestamp da msg do usuário < timestamp da última msg do assistente no histórico)
        _ultima_msg_assistente = next(
            (m for m in reversed(history) if m.get("role") == "assistant"), None
        )
        _ultima_msg_usuario = next(
            (m for m in reversed(history) if m.get("role") == "user"), None
        )
        # Mensagens de espera/concordância enviadas antes da simulação chegar → ignora
        _RE_AGUARDANDO = re.compile(
            r'^(ok|okay|ok[ao]|tá|ta|tá\s*bom|ta\s*bom|tudo\s*bem|blz|beleza|'
            r'certo|claro|sim|s|entendi|entendido|pode|tô|to|aguardo|'
            r'tá\s*certo|ta\s*certo|tá\s*ótimo|ta\s*otimo|combinado|'
            r'pode\s*ser|tudo\s*bem|show|legal|perfeito|ótimo|otimo|'
            r'tô\s*aqui|to\s*aqui|estou\s*aqui|vou\s*aguardar|aguardando)[\.\!\,\s]*$',
            re.I
        )
        if _ultima_msg_assistente and _ultima_msg_usuario:
            from app.services.followup import _parse_dt as _pdt
            _ts_user = _pdt(_ultima_msg_usuario.get("created_at", ""))
            _ts_bot  = _pdt(_ultima_msg_assistente.get("created_at", ""))
            if _ts_user < _ts_bot:
                # Mensagem chegou antes da simulação ser enviada (era resposta ao "um instante")
                return None, State.CONFIRM_SIMULATION, {}

        # Concordância/espera sem ter visto a simulação → ignora silenciosamente
        if _RE_AGUARDANDO.match(t):
            # Só ignora se a última mensagem do assistente foi o aviso de "um instante"
            _ultimo_bot_txt = (_ultima_msg_assistente or {}).get("content", "").lower()
            if "instante" in _ultimo_bot_txt or "aguard" in _ultimo_bot_txt or "simular" in _ultimo_bot_txt:
                return None, State.CONFIRM_SIMULATION, {}

        # Redireciona para outra simulação se pedir outro prazo/parcela OU reclamar que está alto/caro
        quer_valor_maior = bool(re.search(
            r"(tem\s*s[oó]\s*isso|s[oó]\s*isso|tem\s*mais|n[aã]o\s*tem\s*mais|"
            r"valor\s*maior|mais\s*valor|n[aã]o\s*tem\s*(um\s*)?valor\s*maior|"
            r"[eé]\s*esse\s*mesmo|tem\s*outro\s*valor|n[aã]o\s*d[aá]\s*mais|"
            r"algum\s*valor\s*maior|disponivel\s*so\s*isso|so\s*esse\s*valor|"
            r"n[aã]o\s*tem\s*disponivel|mais\s*nada)", t))
        quer_juros_alto = bool(re.search(r"\b(juro\s*(alt[ao]|car[ao]|elevad|muito|absurd)|taxa\s*(alt[ao]|car[ao]|elevad|muito)|muito\s*juro|juro\s*absurd|juro\s*pesad)\b", t))
        quer_outro_prazo = bool(re.search(
            r"\b(outro\s*prazo|outros\s*prazos|ver\s*prazo|mudar\s*prazo|trocar\s*prazo|"
            r"prazo\s*diferente|outr[ao]\s*prazo|opç[oõ]es?\s*de\s*prazo|"
            r"prazo\s*maior|maior\s*prazo|prazo\s*m[aá]ximo|m[aá]ximo\s*prazo|"
            r"mais\s*meses|mais\s*prazo|prazo\s*mais\s*(long|extens|grand)|"
            r"tem\s*prazo\s*maior|tem\s*um\s*prazo\s*maior|prazo\s*maior\s*tem)\b", t))
        quer_outra_parcela = bool(re.search(r"\b(parcela\s*(alt[ao]|cara|muito|menor|diferente|reduz|abaixa)|valor\s*menor|reduz|abaixa|parcela\s*menor|outra\s*parcela|outro\s*valor\s*de\s*parcela)\b", t))
        quer_outros = quer_juros_alto or quer_outro_prazo or quer_outra_parcela
        quer_prosseguir = bool(re.search(
            r"\b(sim|s|confirmo|prosseguir|prossegue|aceito|aceita|vamos|quero|ok|okay|"
            r"blz|beleza|certo|pode|podemos|pode\s*fazer|pode\s*ser|pode\s*ir|claro|com\s*certeza|"
            r"t[oó]|t[aá]\s*bom|fechado|fechar|bora|manda|manda\s*ver|vai|"
            r"concordo|isso|exato|perfeito|ótimo|otimo|legal|show|combinado|"
            r"positivo|afirmativo|continua|continue|segue|seguir|segue\s*em\s*frente|"
            r"vamos\s*l[aá]|tamo|embora|fazer|fa[cç]a|fa[cç]o|"
            r"quero\s*sim|quero\s*fazer|quero\s*prosseguir|pode\s*prosseguir|podemos\s*seguir|"
            r"pode\s*seguir|vamos\s*seguir|pode\s*ir\s*em\s*frente)\b",
            t
        )) and "?" not in text

        if quer_valor_maior:
            # Refaz simulação em 36x para mostrar o maior valor possível
            consult_id = data.get("consult_id", "")
            installments_options = data.get("installments_options", [])
            opcao_36 = next((o for o in installments_options if int(o.get("installmentNumbers", 0)) == 36), None)
            if opcao_36 and consult_id:
                installment_36 = min(float(opcao_36["maxInstallmentValue"]), margem)
                sim36 = await asyncio.to_thread(
                    executar_simulacao, consult_id, config.V8_CONFIG_ID, installment_36, 36
                )
                reply36 = (
                    f"Simulei no prazo máximo de 36x para você! 😊\n\n"
                    f"💰 Valor liberado: {_brl(sim36['disbursement_amount'])}\n"
                    f"📅 Parcelas: {sim36['number_of_installments']}x de {_brl(sim36['installment_value'])}\n\n"
                    f"Podemos prosseguir ou deseja ver outros prazos? 😊"
                )
                return reply36, State.CONFIRM_SIMULATION, {
                    "simulation_id": sim36["simulation_id"],
                    "num_parcelas": sim36["number_of_installments"],
                }
            # Sem opção 36x → vai para customizar
            return (
                f"O valor máximo disponível para você é {_brl(margem)} por parcela. "
                f"Deseja que eu simule com esse valor?"
            ), State.WAITING_CUSTOM_INSTALLMENT, {}

        # Detecta prazo específico mencionado (ex: "18x", "em 18", "dividido em 24", "de 24 meses")
        _PRAZOS_V8_VALIDOS = {8, 10, 12, 18, 24, 36}
        _RE_PRAZO_ESPECIFICO = re.compile(
            r'(\d{1,2})\s*[xX×]|'                                  # "18x", "24X"
            r'(?:em|de|dividido\s*em|parcelado\s*em|no\s*prazo\s*de)\s*(\d{1,2})'
            r'(?:\s*(?:parcelas?|meses?|vezes?|x))?',               # "em 24", "dividido em 24", "de 36 meses"
            re.I
        )
        m_prazo_esp = _RE_PRAZO_ESPECIFICO.search(t)
        prazo_mencionado_esp = None
        if m_prazo_esp:
            raw = int(m_prazo_esp.group(1) or m_prazo_esp.group(2))
            if raw in _PRAZOS_V8_VALIDOS:
                prazo_mencionado_esp = raw

        if prazo_mencionado_esp:
            consult_id = data.get("consult_id", "")
            installments_options = data.get("installments_options", [])
            num_atual = int(data.get("num_parcelas", 0))
            opcao_p = next((o for o in installments_options if int(o.get("installmentNumbers", 0)) == prazo_mencionado_esp), None)
            if opcao_p and consult_id:
                vp = min(float(opcao_p["maxInstallmentValue"]), margem)
                sim_p = await asyncio.to_thread(
                    executar_simulacao, consult_id, config.V8_CONFIG_ID, vp, prazo_mencionado_esp
                )
                prefixo = (f"A simulação atual é em {num_atual}x. " if num_atual and num_atual != prazo_mencionado_esp else "")
                return (
                    f"{prefixo}Aqui está a simulação em {prazo_mencionado_esp}x:\n\n"
                    f"💰 Valor liberado: {_brl(sim_p['disbursement_amount'])}\n"
                    f"📅 Parcelas: {sim_p['number_of_installments']}x de {_brl(sim_p['installment_value'])}\n\n"
                    f"Podemos prosseguir com essa? 😊"
                ), State.CONFIRM_SIMULATION, {"simulation_id": sim_p["simulation_id"], "num_parcelas": sim_p["number_of_installments"]}
            elif not opcao_p:
                return (
                    f"Esse prazo não está disponível. Os prazos aceitos são: 8x, 10x, 12x, 18x, 24x ou 36x. "
                    f"Qual prefere?"
                ), State.WAITING_CUSTOM_TERM, {}

        # Cliente quer a primeira simulação
        _RE_PRIMEIRA = re.compile(
            r'\b(primeira|anterior|aquela|original|a\s*de\s*antes|volta\s*a|de\s*volta|'
            r'pode\s*ser\s*(a\s*)?(primeira|essa|aquela))\b', re.I
        )
        if _RE_PRIMEIRA.search(t):
            first_sim = data.get("first_sim")
            if first_sim:
                return (
                    f"Claro! Aqui está a primeira simulação novamente:\n\n"
                    f"💰 Valor liberado: {_brl(first_sim['disbursement_amount'])}\n"
                    f"📅 Parcelas: {first_sim['number_of_installments']}x de {_brl(first_sim['installment_value'])}\n\n"
                    f"Podemos prosseguir com essa? 😊"
                ), State.CONFIRM_SIMULATION, {"simulation_id": first_sim["simulation_id"]}

        if quer_juros_alto:
            return (
                f"O crédito CLT é uma das linhas com melhores condições do mercado. 😊 "
                f"Para reduzir o valor da parcela, me diz qual valor ficaria melhor para você?\n"
                f"(Parcela máxima disponível: {_brl(margem)})"
            ), State.WAITING_CUSTOM_INSTALLMENT, {}
        if quer_outra_parcela:
            return (
                f"Claro! Qual valor de parcela ficaria melhor para você?\n"
                f"(Parcela máxima disponível: {_brl(margem)})"
            ), State.WAITING_CUSTOM_INSTALLMENT, {}
        if quer_outro_prazo:
            # "prazo maior / máximo / mais meses" → simula 36x direto
            quer_prazo_maior = bool(re.search(
                r"\b(prazo\s*maior|maior\s*prazo|prazo\s*m[aá]ximo|m[aá]ximo\s*prazo|"
                r"mais\s*meses|mais\s*prazo|prazo\s*maior\s*tem|tem\s*prazo\s*maior)\b", t))
            if quer_prazo_maior:
                consult_id = data.get("consult_id", "")
                installments_options = data.get("installments_options", [])
                opcao_36 = next((o for o in installments_options if int(o.get("installmentNumbers", 0)) == 36), None)
                if opcao_36 and consult_id:
                    installment_36 = float(opcao_36.get("installmentValue", 0))
                    sim36 = await asyncio.to_thread(
                        executar_simulacao, consult_id, config.V8_CONFIG_ID, installment_36, 36
                    )
                    reply36 = (
                        f"Simulei no prazo máximo de 36x para você! 😊\n\n"
                        f"💰 Valor liberado: {_brl(sim36['disbursement_amount'])}\n"
                        f"📅 Parcelas: {sim36['number_of_installments']}x de {_brl(sim36['installment_value'])}\n\n"
                        f"Podemos prosseguir ou deseja ver outros prazos? 😊"
                    )
                    return reply36, State.CONFIRM_SIMULATION, {
                        "simulation_id": sim36["simulation_id"],
                        "num_parcelas": sim36["number_of_installments"],
                    }
            return "Claro! Qual prazo prefere? (12x, 18x, 24x ou 36x)", State.WAITING_CUSTOM_TERM, {}
        if quer_prosseguir:
            limpar = {k: None for k in ["address_cep","address_street","address_number",
                                         "address_neighborhood","address_city","address_state",
                                         "rg_number","rg_date","pix_key","marital_status"]}
            return "Ótimo! Para finalizar a proposta, preciso do seu endereço. Qual o CEP?", State.WAITING_ADDRESS_CEP, limpar
        # Pergunta ou objeção → responde via GPT
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "O cliente fez uma pergunta ou objeção. Responda EXATAMENTE o que ele perguntou usando o FAQ — "
            "ex: se perguntou sobre juros, informe a taxa; se reclamou do juros alto, explique que 6,99% a.m. é uma das menores do mercado e sugira simular outro valor de parcela. "
            "NÃO mencione o que já foi feito (simulação, CPF, margem). Depois pergunte se deseja prosseguir com a proposta ou simular outro valor de parcela.")
        return reply, State.CONFIRM_SIMULATION, {}

    # ── WAITING_CUSTOM_INSTALLMENT ────────────────────────────────────────────
    if state == State.WAITING_CUSTOM_INSTALLMENT:
        numeros = re.findall(r"[\d]+(?:[.,]\d+)?", text)
        margem = data.get("margem_disponivel", 0)
        if not numeros:
            reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
                f"O cliente está escolhendo o valor da parcela (máximo {_brl(margem)}). "
                "Responda a dúvida ou objeção usando o FAQ (ex: se disser 'juros alto', explique que 6,99% ao mês é uma das menores taxas do mercado). "
                f"Depois pergunte qual valor de parcela seria confortável (máximo {_brl(margem)}).")
            return reply, State.WAITING_CUSTOM_INSTALLMENT, {}
        valor = float(numeros[0].replace(",", "."))
        if valor > margem:
            return (
                f"O valor máximo disponível é {_brl(margem)}. Qual valor dentro desse limite deseja simular?"
            ), State.WAITING_CUSTOM_INSTALLMENT, {}
        return (
            f"Ótimo! Parcelas de {_brl(valor)}. Qual prazo prefere? 😊\n(12x, 18x, 24x, 36x)"
        ), State.WAITING_CUSTOM_TERM, {"custom_installment_value": valor}

    # ── WAITING_CUSTOM_TERM ───────────────────────────────────────────────────
    if state == State.WAITING_CUSTOM_TERM:
        _PRAZOS_V8 = {8, 10, 12, 18, 24, 36}
        numeros = re.findall(r"\d+", text)
        if not numeros:
            reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
                "O cliente está escolhendo o prazo. Opções disponíveis: 12x, 18x, 24x, 36x. "
                "Responda a dúvida usando o FAQ e depois pergunte qual prazo prefere.")
            return reply, State.WAITING_CUSTOM_TERM, {}
        num_parcelas = int(numeros[0])
        if num_parcelas not in _PRAZOS_V8:
            return (
                "Esse prazo não está disponível, os possíveis prazos são 12x, 18x, 24x, 36x. "
                "Qual desses melhor te atenderia?"
            ), State.WAITING_CUSTOM_TERM, {}
        installment_value = float(data.get("custom_installment_value", data.get("margem_disponivel", 0)))
        return await _rodar_simulacao_custom(user, data, installment_value, num_parcelas)

    # ── COLETA DE ENDEREÇO ────────────────────────────────────────────────────
    if state == State.WAITING_ADDRESS_CEP:
        cep = re.sub(r"\D", "", text)
        if len(cep) == 8:
            # Consulta ViaCEP e auto-preenche endereço, RG e estado civil
            addr = await asyncio.to_thread(_buscar_cep, cep)
            cpf_digits = re.sub(r"\D", "", user.get("cpf") or data.get("cpf", ""))
            rg_auto = cpf_digits[3:] if len(cpf_digits) >= 8 else cpf_digits
            auto_data = {
                "address_cep":          cep,
                "address_street":       addr["street"],
                "address_number":       "01",
                "address_neighborhood": addr["neighborhood"],
                "address_city":         addr["city"],
                "address_state":        addr["state"],
                "rg_number":            rg_auto,
                "rg_date":              "2021-03-15",
                "marital_status":       "single",
            }
            return "Qual sua chave PIX? (CPF, celular, email ou chave aleatória)", State.WAITING_PIX_KEY, auto_data
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "Responda a dúvida do cliente usando o FAQ. Depois peça o CEP (8 dígitos) para continuar.")
        return reply, State.WAITING_ADDRESS_CEP, {}

    if state == State.WAITING_PIX_KEY:
        if _parece_pergunta(text):
            reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
                "Responda a dúvida do cliente usando o FAQ. Depois peça a chave PIX (CPF, celular, email ou chave aleatória).")
            return reply, State.WAITING_PIX_KEY, {}
        # Cliente digitou "cpf" ou "meu cpf" → usa o CPF armazenado como chave
        _RE_QUER_CPF = re.compile(r'^(meu\s*)?cpf[\s\.]*$', re.I)
        pix_key_raw = text.strip()
        if _RE_QUER_CPF.match(pix_key_raw):
            pix_key_raw = re.sub(r"\D", "", user.get("cpf") or data.get("cpf", "")) or pix_key_raw
        merged = {**data, "pix_key": pix_key_raw}
        return await _gerar_proposta(user, merged)

    # ── FACTA: WAITING_CONSENT ────────────────────────────────────────────────
    if state == State.FACTA_WAITING_CONSENT:
        t = text.lower().strip()
        palavras = set(re.findall(r"[a-záéíóúâêîôûãõàèìòùç]+", t))
        if bool(palavras & {"sim", "ok", "autorizei", "ja", "já", "autorizado", "aceito", "feito", "pronto"}):
            # Verifica se realmente autorizou e segue para simulação
            return await _verificar_consent_e_simular(user, data)
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "O cliente ainda não confirmou a autorização. Explique que ele precisa clicar no link enviado via WhatsApp e depois me avisar aqui. Seja breve.")
        return reply, State.FACTA_WAITING_CONSENT, {}

    # ── FACTA: WAITING_ALT_PHONE ──────────────────────────────────────────────
    if state == State.FACTA_WAITING_ALT_PHONE:
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 10:
            celular_alt = _formatar_celular_facta(digits)
            cpf = user.get("cpf") or data.get("cpf", "")
            nome = user.get("name", "")
            token = data.get("facta_token") or await asyncio.to_thread(get_facta_token)
            resp = await asyncio.to_thread(enviar_termo, token, cpf, nome, celular_alt)
            print(f"[facta] enviar_termo alt_phone={celular_alt} resposta: {resp}", flush=True)
            msg_api = str(resp.get("mensagem", "")).lower() if isinstance(resp, dict) else ""
            if "telefone j" in msg_api and "outro cpf" in msg_api:
                phone_digits = re.sub(r"\D", "", user.get("phone", ""))
                if phone_digits.startswith("55"):
                    phone_digits = phone_digits[2:]
                exemplo = f"({phone_digits[:2]}) 9{phone_digits[2:6]}-{phone_digits[6:]}" if len(phone_digits) >= 10 else "35 99999-9999"
                return (
                    "Esse número também já está em uso. Você tem outro WhatsApp disponível? "
                    f"Me manda no formato (DDD) 9XXXX-XXXX — por exemplo: {exemplo}."
                ), State.FACTA_WAITING_ALT_PHONE, {"facta_token": token}
            return (
                "Para consultar seu crédito CLT, precisamos de uma autorização rápida. "
                "Enviamos um link para você através de outro número: +55 51 3021-7836. "
                "Acesse esse número, clique no link e realize a autorização. "
                "Depois, me avise aqui assim que concluir."
            ), State.FACTA_WAITING_CONSENT, {"facta_token": token}
        return (
            "Por favor, informe um número de WhatsApp válido com DDD no formato (DDD) 9XXXX-XXXX:"
        ), State.FACTA_WAITING_ALT_PHONE, {}

    # ── FACTA: WAITING_PHONE ──────────────────────────────────────────────────
    if state == State.FACTA_WAITING_PHONE:
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 10:
            novo_celular = _formatar_celular_facta(digits)
            merged = {**data, "facta_celular_override": novo_celular}
            return await _gerar_proposta_facta(user, merged)
        return "Por favor, informe um número de celular válido com DDD (ex: 35 99999-9999):", State.FACTA_WAITING_PHONE, {}

    # ── FACTA: CONFIRM_SIMULATION ─────────────────────────────────────────────
    if state == State.FACTA_CONFIRM_SIMULATION:
        t = text.lower().strip()

        # Detecta mensagem enfileirada (enviada antes da simulação chegar)
        _ultima_msg_assistente = next(
            (m for m in reversed(history) if m.get("role") == "assistant"), None
        )
        _ultima_msg_usuario = next(
            (m for m in reversed(history) if m.get("role") == "user"), None
        )
        if _ultima_msg_assistente and _ultima_msg_usuario:
            from app.services.followup import _parse_dt as _pdt
            _ts_user = _pdt(_ultima_msg_usuario.get("created_at", ""))
            _ts_bot  = _pdt(_ultima_msg_assistente.get("created_at", ""))
            if _ts_user < _ts_bot:
                return None, State.FACTA_CONFIRM_SIMULATION, {}

        # Concordância/espera sem ter visto a simulação → ignora silenciosamente
        if _RE_AGUARDANDO.match(t):
            _ultimo_bot_txt = (_ultima_msg_assistente or {}).get("content", "").lower()
            if "instante" in _ultimo_bot_txt or "aguard" in _ultimo_bot_txt or "simular" in _ultimo_bot_txt:
                return None, State.FACTA_CONFIRM_SIMULATION, {}

        quer_valor_maior = bool(re.search(
            r"(tem\s*s[oó]\s*isso|s[oó]\s*isso|tem\s*mais|n[aã]o\s*tem\s*mais|"
            r"valor\s*maior|mais\s*valor|n[aã]o\s*tem\s*(um\s*)?valor\s*maior|"
            r"[eé]\s*esse\s*mesmo|tem\s*outro\s*valor|n[aã]o\s*d[aá]\s*mais|"
            r"algum\s*valor\s*maior|disponivel\s*so\s*isso|so\s*esse\s*valor|"
            r"n[aã]o\s*tem\s*disponivel|mais\s*nada)", t))
        quer_juros_alto_f = bool(re.search(r"\b(juro\s*(alt[ao]|car[ao]|elevad|muito|absurd)|taxa\s*(alt[ao]|car[ao]|elevad|muito)|muito\s*juro|juro\s*absurd|juro\s*pesad)\b", t))
        quer_outro_prazo_f = bool(re.search(
            r"\b(outro\s*prazo|outros\s*prazos|ver\s*prazo|mudar\s*prazo|trocar\s*prazo|"
            r"prazo\s*diferente|outr[ao]\s*prazo|opç[oõ]es?\s*de\s*prazo|"
            r"prazo\s*maior|maior\s*prazo|prazo\s*m[aá]ximo|m[aá]ximo\s*prazo|"
            r"mais\s*meses|mais\s*prazo|prazo\s*mais\s*(long|extens|grand)|"
            r"tem\s*prazo\s*maior|tem\s*um\s*prazo\s*maior|prazo\s*maior\s*tem)\b", t))
        quer_outra_parcela_f = bool(re.search(r"\b(parcela\s*(alt[ao]|cara|muito|menor|diferente|reduz|abaixa)|valor\s*menor|reduz|abaixa|parcela\s*menor|outra\s*parcela|outro\s*valor\s*de\s*parcela)\b", t))
        quer_outros = quer_juros_alto_f or quer_outro_prazo_f or quer_outra_parcela_f
        quer_prosseguir = bool(re.search(
            r"\b(sim|s|confirmo|prosseguir|prossegue|aceito|aceita|vamos|quero|ok|okay|"
            r"blz|beleza|certo|pode|podemos|pode\s*fazer|pode\s*ser|pode\s*ir|claro|com\s*certeza|"
            r"t[oó]|t[aá]\s*bom|fechado|fechar|bora|manda|manda\s*ver|vai|"
            r"concordo|isso|exato|perfeito|ótimo|otimo|legal|show|combinado|"
            r"positivo|afirmativo|continua|continue|segue|seguir|segue\s*em\s*frente|"
            r"vamos\s*l[aá]|tamo|embora|fazer|fa[cç]a|fa[cç]o|"
            r"quero\s*sim|quero\s*fazer|quero\s*prosseguir|pode\s*prosseguir|podemos\s*seguir|"
            r"pode\s*seguir|vamos\s*seguir|pode\s*ir\s*em\s*frente)\b",
            t
        )) and "?" not in text

        if quer_valor_maior:
            # Refaz simulação Facta em 36x com margem máxima
            margem_facta = float(data.get("facta_margem") or 0)
            if margem_facta > 0:
                return await _rodar_simulacao_facta_custom(user, data, margem_facta, 36)
            return (
                f"Infelizmente não há margem maior disponível no momento. "
                f"Deseja prosseguir com a proposta atual?"
            ), State.FACTA_CONFIRM_SIMULATION, {}

        # Detecta prazo específico mencionado (ex: "18x", "em 18", "dividido em 24")
        _PRAZOS_FACTA_VALIDOS = {12, 18, 24, 36}
        _RE_PRAZO_ESP_F = re.compile(
            r'(\d{1,2})\s*[xX×]|'
            r'(?:em|de|dividido\s*em|parcelado\s*em|no\s*prazo\s*de)\s*(\d{1,2})'
            r'(?:\s*(?:parcelas?|meses?|vezes?|x))?',
            re.I
        )
        m_prazo_esp_f = _RE_PRAZO_ESP_F.search(t)
        prazo_esp_f = None
        if m_prazo_esp_f:
            raw_f = int(m_prazo_esp_f.group(1) or m_prazo_esp_f.group(2))
            if raw_f in _PRAZOS_FACTA_VALIDOS:
                prazo_esp_f = raw_f

        if prazo_esp_f:
            facta_simulacoes = data.get("facta_simulacoes", [])
            num_atual_f = int((data.get("facta_sim_selecionada") or {}).get("prazo") or 0)
            sim_p_f = next((s for s in facta_simulacoes if int(s.get("prazo", 0)) == prazo_esp_f), None)
            if sim_p_f:
                vl_f = float(sim_p_f.get("valor_liquido") or sim_p_f.get("valorLiquido") or sim_p_f.get("valorLiberado") or 0)
                vp_f = float(sim_p_f.get("parcela") or sim_p_f.get("valorParcela") or 0)
                prefixo_f = (f"A simulação atual é em {num_atual_f}x. " if num_atual_f and num_atual_f != prazo_esp_f else "")
                return (
                    f"{prefixo_f}Aqui está a simulação em {prazo_esp_f}x:\n\n"
                    f"💰 Valor liberado: *{_brl(vl_f)}*\n"
                    f"📅 Parcelas: {prazo_esp_f}x de {_brl(vp_f)}\n\n"
                    f"Podemos prosseguir com essa? 😊"
                ), State.FACTA_CONFIRM_SIMULATION, {"facta_sim_selecionada": sim_p_f}
            else:
                return await _rodar_simulacao_facta_custom(user, data, float(data.get("facta_margem") or 0), prazo_esp_f)

        # Cliente quer a primeira simulação
        if _RE_PRIMEIRA.search(t):
            facta_sim_inicial = data.get("facta_sim_inicial")
            if facta_sim_inicial:
                vl_i = float(facta_sim_inicial.get("valor_liquido") or facta_sim_inicial.get("valorLiquido") or facta_sim_inicial.get("valorLiberado") or 0)
                vp_i = float(facta_sim_inicial.get("parcela") or facta_sim_inicial.get("valorParcela") or 0)
                prazo_i = int(facta_sim_inicial.get("prazo") or 18)
                return (
                    f"Claro! Aqui está a primeira simulação novamente:\n\n"
                    f"💰 Valor liberado: *{_brl(vl_i)}*\n"
                    f"📅 Parcelas: {prazo_i}x de {_brl(vp_i)}\n\n"
                    f"Podemos prosseguir com essa? 😊"
                ), State.FACTA_CONFIRM_SIMULATION, {"facta_sim_selecionada": facta_sim_inicial}

        if quer_juros_alto_f:
            margem_f = float(data.get("facta_margem") or 0)
            return (
                f"O crédito CLT é uma das linhas com melhores condições do mercado. 😊 "
                f"Para reduzir o valor da parcela, me diz qual valor ficaria melhor para você?"
                + (f"\n(Parcela máxima disponível: {_brl(margem_f)})" if margem_f > 0 else "")
            ), State.FACTA_WAITING_CUSTOM_INSTALLMENT, {}
        if quer_outra_parcela_f:
            margem_f = float(data.get("facta_margem") or 0)
            limite_txt = f"\n(Parcela máxima disponível: {_brl(margem_f)})" if margem_f > 0 else ""
            return (
                f"Claro! Qual valor de parcela ficaria melhor para você?{limite_txt}"
            ), State.FACTA_WAITING_CUSTOM_INSTALLMENT, {}
        if quer_outro_prazo_f:
            # "prazo maior / máximo / mais meses" → simula 36x direto
            quer_prazo_maior_f = bool(re.search(
                r"\b(prazo\s*maior|maior\s*prazo|prazo\s*m[aá]ximo|m[aá]ximo\s*prazo|"
                r"mais\s*meses|mais\s*prazo|prazo\s*maior\s*tem|tem\s*prazo\s*maior)\b", t))
            if quer_prazo_maior_f:
                margem_facta = float(data.get("facta_margem") or 0)
                if margem_facta > 0:
                    return await _rodar_simulacao_facta_custom(user, data, margem_facta, 36)
            return "Claro! Qual prazo prefere? (12x, 18x, 24x ou 36x)", State.FACTA_WAITING_CUSTOM_TERM, {}
        if quer_prosseguir:
            limpar = {k: None for k in ["address_cep","address_street","address_number",
                                         "address_neighborhood","address_city","address_state",
                                         "rg_number","rg_date","pix_key","marital_status"]}
            return "Ótimo! Para finalizar a proposta, preciso do seu endereço. Qual o CEP?", State.FACTA_WAITING_CEP, limpar
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "O cliente fez uma pergunta ou objeção. Responda usando o FAQ. "
            "Depois pergunte se deseja prosseguir com a proposta ou simular outro valor.")
        return reply, State.FACTA_CONFIRM_SIMULATION, {}

    # ── FACTA: WAITING_CUSTOM_INSTALLMENT ─────────────────────────────────────
    if state == State.FACTA_WAITING_CUSTOM_INSTALLMENT:
        numeros = re.findall(r"[\d]+(?:[.,]\d+)?", text)
        if not numeros:
            reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
                "O cliente está escolhendo o valor da parcela. Responda a dúvida e peça qual valor de parcela prefere.")
            return reply, State.FACTA_WAITING_CUSTOM_INSTALLMENT, {}
        valor = float(numeros[0].replace(",", "."))
        return (
            f"Ótimo! Parcelas de {_brl(valor)}. Qual prazo prefere?\n(12x, 18x, 24x, 36x)"
        ), State.FACTA_WAITING_CUSTOM_TERM, {"facta_custom_parcela": valor}

    # ── FACTA: WAITING_CUSTOM_TERM ────────────────────────────────────────────
    if state == State.FACTA_WAITING_CUSTOM_TERM:
        _PRAZOS_FACTA = {12, 18, 24, 36}
        numeros = re.findall(r"\d+", text)
        if not numeros:
            reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
                "O cliente está escolhendo o prazo. Opções: 12x, 18x, 24x, 36x. Pergunte qual prefere.")
            return reply, State.FACTA_WAITING_CUSTOM_TERM, {}
        num_parcelas = int(numeros[0])
        if num_parcelas not in _PRAZOS_FACTA:
            return (
                "Esse prazo não está disponível, os possíveis prazos são 12x, 18x, 24x, 36x. "
                "Qual desses melhor te atenderia?"
            ), State.FACTA_WAITING_CUSTOM_TERM, {}
        valor_parcela = float(data.get("facta_custom_parcela") or data.get("custom_installment_value") or 0)
        return await _rodar_simulacao_facta_custom(user, data, valor_parcela, num_parcelas)

    # ── FACTA: WAITING_CEP ────────────────────────────────────────────────────
    if state == State.FACTA_WAITING_CEP:
        cep = re.sub(r"\D", "", text)
        if len(cep) == 8:
            addr = await asyncio.to_thread(_buscar_cep, cep)
            cpf_digits = re.sub(r"\D", "", user.get("cpf") or data.get("cpf", ""))
            rg_auto = cpf_digits[3:] if len(cpf_digits) >= 8 else cpf_digits
            auto_data = {
                "address_cep":          cep,
                "address_street":       addr["street"],
                "address_number":       "01",
                "address_neighborhood": addr["neighborhood"],
                "address_city":         addr["city"],
                "address_state":        addr["state"],
                "rg_number":            rg_auto,
                "rg_date":              "2021-03-15",
                "marital_status":       "single",
            }
            return "Qual sua chave PIX? (CPF, celular, email ou chave aleatória)", State.FACTA_WAITING_PIX_KEY, auto_data
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "Responda a dúvida do cliente usando o FAQ. Depois peça o CEP (8 dígitos) para continuar.")
        return reply, State.FACTA_WAITING_CEP, {}

    # ── FACTA: WAITING_PIX_KEY ────────────────────────────────────────────────
    if state == State.FACTA_WAITING_PIX_KEY:
        if _parece_pergunta(text):
            reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
                "Responda a dúvida do cliente usando o FAQ. Depois peça a chave PIX (CPF, celular, email ou chave aleatória).")
            return reply, State.FACTA_WAITING_PIX_KEY, {}
        # Cliente digitou "cpf" ou "meu cpf" → usa o CPF armazenado como chave
        pix_key_raw_f = text.strip()
        if re.match(r'^(meu\s*)?cpf[\s\.]*$', pix_key_raw_f, re.I):
            pix_key_raw_f = re.sub(r"\D", "", user.get("cpf") or data.get("cpf", "")) or pix_key_raw_f
        merged = {**data, "pix_key": pix_key_raw_f}
        return await _gerar_proposta_facta(user, merged)

    # ── PROPOSAL_SENT ─────────────────────────────────────────────────────────
    if state == State.PROPOSAL_SENT:
        t_lower = text.lower()

        # Cliente indica que já assinou/finalizou a proposta
        _RE_CONCLUIU = re.compile(
            r'\b(finaliz|pronto|pronta|conclu[íi]|j[aá]\s*fiz|assin[eio]|realizei|feito|feita|terminei|terminou|fiz|ok\s*fiz|já\s*assinei)\b',
            re.I
        )
        if _RE_CONCLUIU.search(t_lower):
            return "Certo, irei verificar. Só um instante! ✅", State.HUMAN_HANDOFF, {}

        # Link não está abrindo
        _RE_LINK_ERRO = re.compile(
            r'\b(n[aã]o\s*(abre|abri|abriu|est[aá]\s*abrindo|consigo\s*abrir|consigo\s*acessar)|link\s*(n[aã]o|quebr|inválid|expirou|deu\s*erro)|erro\s*no\s*link|n[aã]o\s*abre)\b',
            re.I
        )
        if _RE_LINK_ERRO.search(t_lower):
            formalization_url = data.get("formalization_url", "")
            return (
                f"Segue o link novamente:\n{formalization_url}\n\n"
                f"Por favor, feche o navegador do celular completamente e abra novamente — isso resolve na maioria dos casos! 😊"
            ), State.PROPOSAL_SENT, {}

        # Cliente pergunta sobre validade/expiração
        _RE_VALIDADE = re.compile(
            r'\b(valid|expir|venc|prazo|quanto\s*tempo|tem\s*prazo|vai\s*expir)\b',
            re.I
        )
        if _RE_VALIDADE.search(t_lower):
            return (
                "O link da proposta tem validade de *10 horas* após o envio. "
                "Caso expire, é só me chamar que gero um novo link! 😊"
            ), State.PROPOSAL_SENT, {}

        # Outros casos: GPT
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "Responda a mensagem do cliente sobre a proposta enviada. Se pedir algo novo, pergunte se quer iniciar uma nova simulação.")
        return reply, State.PROPOSAL_SENT, {}

    # ── HUMAN_HANDOFF ─────────────────────────────────────────────────────────
    if state == State.HUMAN_HANDOFF:
        reply = await asyncio.to_thread(_ask_gpt, state, data, user, history,
            "Responda a mensagem do cliente. Se pedir algo novo, pergunte se quer iniciar uma nova simulação.")
        return reply, state, {}

    # Fallback
    reply = await asyncio.to_thread(_ask_gpt, state, data, user, history, "")
    return reply, State.GREETING, {}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _buscar_cep(cep: str) -> dict:
    """Consulta ViaCEP e retorna campos de endereço com fallbacks."""
    import requests as req
    try:
        r = req.get(
            f"https://viacep.com.br/ws/{cep}/json/",
            headers={"Accept": "application/json"},
            timeout=5,
        )
        if r.ok:
            d = r.json()
            if not d.get("erro"):
                return {
                    "street":       d.get("logradouro", "").strip() or "Rua um",
                    "neighborhood": d.get("bairro", "").strip() or "Centro",
                    "city":         d.get("localidade", "").strip() or "Sao Paulo",
                    "state":        d.get("uf", "").strip().upper() or "SP",
                }
    except Exception as e:
        print(f"[viacep] erro: {e}", flush=True)
    return {"street": "Rua um", "neighborhood": "Centro", "city": "Sao Paulo", "state": "SP"}


def _parece_pergunta(text: str) -> bool:
    """Retorna True se o texto parece uma pergunta ou comentário fora do fluxo."""
    t = text.lower().strip()
    if "?" in t:
        return True
    if bool(re.search(r"^(qual|como|o que|quando|por que|pode|tem|é|quanto|quem|preciso|quero saber)", t)):
        return True
    # Comentários/objeções comuns
    if bool(re.search(r"\b(juro|juros|taxa|segur|cancel|demit|fgts|pix|document|burocracia|prazo|parcela|alto|caro|barato)\b", t)):
        return True
    return False


async def _rodar_simulacao_padrao(user: dict, data: dict) -> tuple:
    """Busca dados do cliente via API e executa simulação padrão de 18 meses."""
    try:
        cpf = user.get("cpf") or data.get("cpf", "")
        print(f"[simulacao] INICIO user_id={user['id']} phone={user.get('phone')} cpf={cpf} crm_conv={data.get('crm_conversation_id')}", flush=True)

        # Valores padrão — serão sobrescritos pelo V8 ou Multicorban
        nome = user.get("name", "")
        email = user.get("email", "")
        birth_date = ""
        gender = "male"
        celular = ""

        # Tenta buscar dados no V8
        try:
            cliente = await asyncio.to_thread(buscar_dados_cliente, cpf)
            nome = cliente.get("name", nome)
            email = cliente.get("email") or email
            birth_date = (cliente.get("birthDate") or "")[:10]
            gender = cliente.get("gender", "male") or "male"
            area_code = str(cliente.get("phoneRegionCode", "11"))
            phone_number = str(cliente.get("phoneNumber", ""))
            if len(phone_number) == 8:
                phone_number = "9" + phone_number
            celular = area_code + phone_number
        except Exception as e_v8:
            # CPF não encontrado no V8 → consulta Multicorban para preencher dados
            print(f"[_rodar_simulacao_padrao] V8 buscar_dados falhou ({e_v8}), consultando Multicorban...", flush=True)
            try:
                mc = await asyncio.to_thread(buscar_dados_cliente_facta, cpf)
                if mc.get("nome"):
                    nome = mc["nome"]
                # Converte DD/MM/YYYY → YYYY-MM-DD
                data_nasc_mc = mc.get("data_nascimento", "")
                if data_nasc_mc:
                    partes = data_nasc_mc.split("/")
                    if len(partes) == 3:
                        birth_date = f"{partes[2]}-{partes[1].zfill(2)}-{partes[0].zfill(2)}"
                # Converte M/F → male/female
                sexo_mc = mc.get("sexo", "M")
                gender = "male" if sexo_mc == "M" else "female"
                # Gera email: primeironome + segundonome + 26@gmail.com
                partes_nome = re.sub(r"[^a-záéíóúâêîôûãõàèìòùçA-Z ]", "", nome).split()
                slug = (partes_nome[0] + (partes_nome[1] if len(partes_nome) > 1 else "")).lower()
                slug = slug.translate(str.maketrans("áéíóúâêîôûãõàèìòùç", "aeiouaeiouaoaeiouc"))
                email = f"{slug}26@gmail.com"
                print(f"[_rodar_simulacao_padrao] Multicorban: nome={nome} nasc={birth_date} genero={gender} email={email}", flush=True)
            except Exception as e_mc:
                print(f"[_rodar_simulacao_padrao] Multicorban falhou ({e_mc}), acionando Facta...", flush=True)
                return await _iniciar_fluxo_facta(user, data)
            # Usa o celular do WhatsApp como fallback
            phone_wa = re.sub(r"\D", "", user.get("phone", ""))
            if phone_wa.startswith("55"):
                phone_wa = phone_wa[2:]
            celular = phone_wa

        # Atualiza usuário com dados reais
        update_user(user["id"], {
            "name": nome,
            "email": email,
            "birth_date": birth_date,
            "gender": gender,
        })

        # Se não conseguiu data de nascimento, não adianta tentar V8 → vai para Facta
        if not birth_date:
            print("[_rodar_simulacao_padrao] birth_date vazio apos V8+Multicorban, acionando Facta...", flush=True)
            return await _iniciar_fluxo_facta(user, data)

        # Executa consulta de margem
        resultado = None
        try:
            resultado = await asyncio.to_thread(
                executar_fluxo_consulta, cpf, nome, email, celular, birth_date, gender
            )
        except ConsultaRejeitadaError as e:
            # V8 rejeitou → vai direto para Facta
            print(f"[_rodar_simulacao_padrao] V8 rejeitou (status={e.status}), acionando Facta...", flush=True)
            return await _iniciar_fluxo_facta(user, data)
        except ConsultaEmAnaliseError as e:
            # Consulta ainda em análise após todas as tentativas → última espera de 40s (4ª tentativa)
            print(f"[_rodar_simulacao_padrao] Consulta em analise apos todas tentativas, aguardando 40s (4a tentativa)...", flush=True)
            await asyncio.sleep(40)
            dados_final = await asyncio.to_thread(buscar_dados_consulta, e.consult_id)
            status_final = dados_final.get("status", "")
            if status_final != "SUCCESS":
                # Ainda não aprovada → encaminha para Facta
                print(f"[_rodar_simulacao_padrao] V8 ainda em {status_final!r} apos espera extra, acionando Facta...", flush=True)
                return await _iniciar_fluxo_facta(user, data)
            resultado = {
                "consult_id": e.consult_id,
                "margem_disponivel": float(dados_final.get("marginBaseValue") or 0),
                "simulation_limit": dados_final.get("simulationLimit", {}),
                "employer_name": dados_final.get("employerName"),
                "employer_document_number": dados_final.get("employerDocumentNumber"),
                "registration_number": dados_final.get("registrationNumber"),
                "admission_date": dados_final.get("admissionDate"),
                "mother_name": dados_final.get("motherName"),
                "recommended_installment_value": float(dados_final.get("recommendedSimulationInstallmentValue") or 0),
                "recommended_installment_number": dados_final.get("recommendedSimulationInstallmentNumber"),
            }

        consult_id = resultado["consult_id"]
        margem = resultado["margem_disponivel"]
        recommended_value = resultado.get("recommended_installment_value", 0)
        recommended_num = resultado.get("recommended_installment_number")

        # Busca opções de parcelas
        installments = await asyncio.to_thread(
            pre_calcular_installments, consult_id, config.V8_CONFIG_ID
        )

        # Usa 18 meses como padrão; se não disponível, usa o de maior prazo
        opcao_18 = next((o for o in installments if int(o["installmentNumbers"]) == 18), None)
        opcao = opcao_18 or (installments[-1] if installments else None)
        if not opcao:
            return "Não encontrei opções de parcelas disponíveis para o seu perfil. 😔", State.GREETING, {}

        num_parcelas = 18 if opcao_18 else int(opcao["installmentNumbers"])
        max_opcao = float(opcao["maxInstallmentValue"])
        # Usa recommended_installment_value da API se disponível (já respeitou a margem do cliente)
        # Senão usa o mínimo entre maxInstallmentValue do produto e a margem real
        if recommended_value and recommended_value > 0:
            installment_value = min(recommended_value, margem)
        else:
            installment_value = min(max_opcao, margem)
        print(f"[simulacao] margem={margem} recommended={recommended_value} maxOpcao={max_opcao} valor_final={installment_value} parcelas={num_parcelas}", flush=True)

        sim = await asyncio.to_thread(
            executar_simulacao, consult_id, config.V8_CONFIG_ID, installment_value, num_parcelas
        )

        reply = (
            f"Simulação realizada! 🎉\n\n"
            f"💰 Valor liberado: {_brl(sim['disbursement_amount'])}\n"
            f"📅 Parcelas: {sim['number_of_installments']}x de {_brl(sim['installment_value'])}\n\n"
            f"Podemos prosseguir ou deseja ver outros prazos? 😊"
        )

        _first_sim = {
            "simulation_id":        sim["simulation_id"],
            "disbursement_amount":  sim["disbursement_amount"],
            "installment_value":    sim["installment_value"],
            "number_of_installments": sim["number_of_installments"],
        }
        return reply, State.CONFIRM_SIMULATION, {
            "consult_id": consult_id,
            "margem_disponivel": margem,
            "simulation_id": sim["simulation_id"],
            "installments_options": installments,
            "employer_name": resultado.get("employer_name"),
            "employer_document_number": resultado.get("employer_document_number"),
            "registration_number": resultado.get("registration_number"),
            "mother_name": resultado.get("mother_name"),
            "client_celular": celular,
            "first_sim": _first_sim,
        }

    except Exception as e:
        import traceback
        print(f"[_rodar_simulacao_padrao] Erro V8: {e}", flush=True)
        try:
            print(traceback.format_exc().encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
        # V8 falhou → tenta Facta como banco secundário
        print("[_rodar_simulacao_padrao] Acionando Facta como fallback...", flush=True)
        return await _iniciar_fluxo_facta(user, data)


async def _rodar_simulacao_custom(user: dict, data: dict, installment_value: float, num_parcelas: int) -> tuple:
    """Executa simulação com valores personalizados pelo cliente."""
    try:
        consult_id = data.get("consult_id")
        if not consult_id:
            return await _rodar_simulacao_padrao(user, data)

        sim = await asyncio.to_thread(
            executar_simulacao, consult_id, config.V8_CONFIG_ID, installment_value, num_parcelas
        )

        reply = (
            f"Simulação realizada! 🎉\n\n"
            f"💰 Valor liberado: {_brl(sim['disbursement_amount'])}\n"
            f"📅 Parcelas: {sim['number_of_installments']}x de {_brl(sim['installment_value'])}\n\n"
            f"Podemos prosseguir ou deseja ver outros prazos? 😊"
        )

        return reply, State.CONFIRM_SIMULATION, {
            **data,
            "simulation_id": sim["simulation_id"],
        }

    except Exception as e:
        print(f"[_rodar_simulacao_custom] Erro: {e}")
        return (
            "Esse prazo não está disponível. Por favor, informe outro prazo: 8x, 10x, 12x, 18x, 24x ou 36x."
        ), State.WAITING_CUSTOM_TERM, {}


async def _gerar_proposta(user: dict, data: dict) -> tuple:
    """Monta dados do tomador e gera proposta final."""
    try:
        # Telefone do cliente (usa o celular capturado da API, ou o WhatsApp)
        celular_api = data.get("client_celular", "")
        phone_raw = re.sub(r"\D", "", celular_api or user.get("phone", ""))
        if phone_raw.startswith("55") and len(phone_raw) >= 12:
            phone_raw = phone_raw[2:]
        area_code = phone_raw[:2] if len(phone_raw) >= 10 else "11"
        phone_number = phone_raw[2:] if len(phone_raw) >= 10 else phone_raw
        if len(phone_number) == 8:
            phone_number = "9" + phone_number

        pix_key = data.get("pix_key", "")
        pix_key_type_raw = _detectar_tipo_pix_inteligente(
            pix_key,
            cpf=user.get("cpf") or data.get("cpf", ""),
            phone=user.get("phone", ""),
        )
        # Converte código numérico (Facta) para o formato texto exigido pela V8
        _V8_PIX_TYPE = {"1": "cpf", "2": "telefone", "3": "e-mail", "4": "chave aleatória"}
        pix_key_type = _V8_PIX_TYPE.get(pix_key_type_raw, "cpf")

        # Chave PIX CPF → remove máscara (V8 aceita só dígitos)
        if pix_key_type_raw == "1":
            pix_key = re.sub(r"\D", "", pix_key)

        # Chave PIX telefone → garante formato +55DDDNUMERO
        if pix_key_type_raw == "2":
            digits = re.sub(r"\D", "", pix_key)
            if not digits.startswith("55"):
                digits = "55" + digits
            pix_key = "+" + digits

        birth_date = str(user.get("birth_date") or data.get("birth_date", ""))[:10]
        gender = user.get("gender") or data.get("gender", "male")

        dados_tomador = {
            "name": user.get("name", ""),
            "email": user.get("email") or data.get("email", ""),
            "phone": {"country_code": "55", "area_code": area_code, "number": phone_number},
            "birth_date": birth_date,
            "mother_name": data.get("mother_name", ""),
            "gender": gender,
            "marital_status": data.get("marital_status", "single"),
            "individual_document_number": user.get("cpf") or data.get("cpf", ""),
            "document_identification_number": data.get("rg_number") or "",
            "document_identification_date": data.get("rg_date") or "",
            "document_issuer": "SSP",
            "document_identification_type": "rg",
            "political_exposition": False,
            "nationality": "Brasileiro",
            "person_type": "natural",
            "address": {
                "postal_code": data.get("address_cep", ""),
                "street": data.get("address_street", ""),
                "number": data.get("address_number", "S/N"),
                "neighborhood": data.get("address_neighborhood", ""),
                "city": data.get("address_city", ""),
                "state": data.get("address_state", ""),
                "complement": "",
            },
            "bank": {"transfer_method": "pix", "pix_key": pix_key, "pix_key_type": pix_key_type},
            "work_data": {
                "employer_name": data.get("employer_name", ""),
                "employer_document_number": re.sub(r"\D", "", str(data.get("employer_document_number", "")))[:8],
                "registration_number": data.get("registration_number", ""),
            },
        }

        resultado = await asyncio.to_thread(gerar_proposta, data["simulation_id"], dados_tomador)

        reply = (
            f"✅ Proposta gerada com sucesso!\n\n"
            f"Segue o link para assinatura digital:\n{resultado['formalization_url']}\n\n"
            f"A assinatura é 100% digital e segura — após assinar, o valor será creditado via PIX. 😊\n\n"
            f"Qualquer dúvida, é só me chamar!"
        )

        return reply, State.PROPOSAL_SENT, {"formalization_url": resultado["formalization_url"]}

    except Exception as e:
        import traceback
        print(f"[_gerar_proposta] Erro: {e}")
        print(traceback.format_exc())
        return "Erro ao gerar proposta. Por favor, entre em contato com nosso atendimento. 🙏", State.ERROR, {}


def _formatar_celular_facta(phone: str) -> str:
    """Converte número de telefone para o formato Facta: (35) 99872-2790."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("55") and len(digits) >= 12:
        digits = digits[2:]
    # 11 dígitos: DDD(2) + 9 + número(8) — formato correto
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    # 10 dígitos: DDD(2) + número(8) sem o 9 — adiciona o 9
    if len(digits) == 10:
        return f"({digits[:2]}) 9{digits[2:6]}-{digits[6:]}"
    return digits


def _extrair_dados_auth(auth: dict, user: dict) -> dict:
    """Extrai dados do cliente da resposta de autorização Facta."""
    dados = auth.get("raw", {}).get("dados_trabalhador", {}).get("dados", [{}])
    d = dados[0] if dados else {}

    nome = d.get("nome") or user.get("name", "")
    data_nasc = d.get("dataNascimento", "")  # DD/MM/YYYY
    sexo = "M" if d.get("sexo_codigo", "1") == "1" else "F"
    nome_mae = d.get("nomeMae", "NAO DECLARADO") or "NAO DECLARADO"

    def _parse_valor(s):
        v = re.sub(r"[^\d,]", "", str(s or "0")).replace(",", ".")
        try:
            return float(v) if v else 0.0
        except ValueError:
            return 0.0

    # valorBaseMargem = base salarial; valorMargemDisponivel = margem real disponível (parcela máxima)
    renda = _parse_valor(d.get("valorBaseMargem", "0"))
    margem_disponivel = _parse_valor(d.get("valorMargemDisponivel", "0"))
    elegivel = str(d.get("elegivel", "")).strip().upper()
    inelegivel = elegivel in ("NÃO", "NAO", "N", "FALSE", "0", "INELEGIVEL", "INELEGÍVEL")

    print(f"[facta] dados_auth: nome={nome} nasc={data_nasc} renda={renda} margem={margem_disponivel} sexo={sexo} elegivel={elegivel}", flush=True)
    return {
        "nome": nome,
        "data_nasc": data_nasc,
        "sexo": sexo,
        "nome_mae": nome_mae,
        "renda": renda,
        "margem_disponivel": margem_disponivel,
        "inelegivel": inelegivel,
    }


def _completar_dados_multicorban(cpf: str, cd: dict) -> dict:
    """Preenche campos ausentes buscando no Multicorban."""
    if cd.get("data_nasc") and cd.get("renda"):
        return cd  # já tem tudo, não precisa buscar
    try:
        print(f"[facta] dados incompletos, buscando Multicorban...", flush=True)
        mc = buscar_dados_cliente_facta(cpf)
        if not cd.get("nome"):
            cd["nome"] = mc.get("nome") or cd["nome"]
        if not cd.get("data_nasc"):
            cd["data_nasc"] = mc.get("data_nascimento", "")
        if not cd.get("renda"):
            cd["renda"] = float(mc.get("renda") or 0.0)
        if cd["nome_mae"] == "NAO DECLARADO":
            cd["nome_mae"] = mc.get("nome_mae", "NAO DECLARADO") or "NAO DECLARADO"
        print(f"[facta] apos multicorban: nasc={cd['data_nasc']} renda={cd['renda']}", flush=True)
    except Exception as e:
        print(f"[facta] multicorban fallback falhou: {e}", flush=True)
    return cd


async def _iniciar_fluxo_facta(user: dict, data: dict) -> tuple:
    """Inicia fluxo Facta: verifica autorização e envia termo se necessário."""
    try:
        cpf = user.get("cpf") or data.get("cpf", "")
        token = await asyncio.to_thread(get_facta_token)
        auth = await asyncio.to_thread(verificar_autorizacao, token, cpf)
        print(f"[facta] autorizado={auth['autorizado']} matricula={auth.get('matricula')}", flush=True)

        if not auth["autorizado"]:
            nome = user.get("name", "")
            celular_wa = _formatar_celular_facta(user.get("phone", ""))
            resp_termo = await asyncio.to_thread(enviar_termo, token, cpf, nome, celular_wa)
            print(f"[facta] enviar_termo resposta: {resp_termo}", flush=True)

            # Telefone já vinculado a outro CPF → pede número alternativo
            msg_api = str(resp_termo.get("mensagem", "")).lower() if isinstance(resp_termo, dict) else ""
            if "telefone j" in msg_api and "outro cpf" in msg_api:
                phone_digits = re.sub(r"\D", "", user.get("phone", ""))
                if phone_digits.startswith("55"):
                    phone_digits = phone_digits[2:]
                exemplo = f"({phone_digits[:2]}) 9{phone_digits[2:6]}-{phone_digits[6:]}" if len(phone_digits) >= 10 else "35 99999-9999"
                return (
                    "Para consultar seu crédito CLT, precisamos de uma autorização rápida.\n"
                    "Esse número já consta em uso. Você tem outro WhatsApp que eu possa usar? "
                    f"Me manda no formato (DDD) 9XXXX-XXXX — por exemplo: {exemplo}."
                ), State.FACTA_WAITING_ALT_PHONE, {"facta_token": token}

            return (
                "Para consultar seu crédito CLT, precisamos de uma autorização rápida. "
                "Enviamos um link para você através de outro número: +55 51 3021-7836. "
                "Acesse esse número, clique no link e realize a autorização. "
                "Depois, me avise aqui assim que concluir."
            ), State.FACTA_WAITING_CONSENT, {"facta_token": token}

        # Extrai dados do cliente direto da resposta de autorização
        cd = _extrair_dados_auth(auth, user)

        # Verifica elegibilidade antes de simular
        if cd.get("inelegivel") or cd.get("margem_disponivel", 0) == 0:
            print(f"[facta] Cliente inelegível ou margem zero, encerrando fluxo Facta", flush=True)
            return (
                "Consultei seu benefício CLT e, infelizmente, não há margem disponível para crédito no momento. "
                "Isso pode ocorrer por descontos já existentes em folha ou pelo seu vínculo empregatício atual. "
                "Caso sua situação mude, pode nos chamar novamente! 😊"
            ), State.GREETING, {}

        # Fallback Multicorban se faltar data_nasc ou renda
        cpf = user.get("cpf") or data.get("cpf", "")
        cd = await asyncio.to_thread(_completar_dados_multicorban, cpf, cd)
        # Salva todas as matrículas disponíveis no data para uso na simulação
        data = {**data, "facta_matriculas": auth.get("matriculas", [])}
        return await _rodar_simulacao_facta(user, data, token, auth["matricula"], cd)

    except Exception as e:
        import traceback
        print(f"[_iniciar_fluxo_facta] Erro: {e}", flush=True)
        try:
            print(traceback.format_exc().encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
        return (
            "No momento não foi possível processar sua solicitação. Tente novamente em instantes. 🙏",
            State.WAITING_CPF, {}
        )


async def _verificar_consent_e_simular(user: dict, data: dict) -> tuple:
    """Verifica se o cliente autorizou e segue para simulação Facta."""
    try:
        cpf = user.get("cpf") or data.get("cpf", "")
        token = await asyncio.to_thread(get_facta_token)
        auth = await asyncio.to_thread(verificar_autorizacao, token, cpf)
        print(f"[facta] re-verificacao autorizado={auth['autorizado']}", flush=True)

        if not auth["autorizado"]:
            return (
                "Ainda não localizei sua autorização. Por favor, clique no link enviado e tente novamente. 😊"
            ), State.FACTA_WAITING_CONSENT, {}

        cd = _extrair_dados_auth(auth, user)

        # Verifica elegibilidade antes de simular
        if cd.get("inelegivel") or cd.get("margem_disponivel", 0) == 0:
            print(f"[facta] Cliente inelegível ou margem zero, encerrando fluxo Facta", flush=True)
            return (
                "Consultei seu benefício CLT e, infelizmente, não há margem disponível para crédito no momento. "
                "Isso pode ocorrer por descontos já existentes em folha ou pelo seu vínculo empregatício atual. "
                "Caso sua situação mude, pode nos chamar novamente! 😊"
            ), State.GREETING, {}

        cpf = user.get("cpf") or data.get("cpf", "")
        cd = await asyncio.to_thread(_completar_dados_multicorban, cpf, cd)
        data = {**data, "facta_matriculas": auth.get("matriculas", [])}
        return await _rodar_simulacao_facta(user, data, token, auth["matricula"], cd)

    except Exception as e:
        print(f"[_verificar_consent_e_simular] Erro: {e}", flush=True)
        return (
            "Erro ao verificar autorização. Tente novamente em instantes. 🙏"
        ), State.FACTA_WAITING_CONSENT, {}


async def _rodar_simulacao_facta(user: dict, data: dict, token: str, matricula: str, cd: dict) -> tuple:
    """Busca simulações na Facta e retorna a melhor opção."""
    try:
        cpf = user.get("cpf") or data.get("cpf", "")
        nome = cd["nome"]
        data_nasc = cd["data_nasc"]
        renda = cd["renda"]
        sexo = cd["sexo"]
        nome_mae = cd["nome_mae"]
        margem = cd.get("margem_disponivel", 0.0)

        print(f"[facta] simulando cpf={cpf} renda={renda} margem={margem} nasc={data_nasc}", flush=True)

        # Usa margem_disponivel como valor_parcela para respeitar o limite real do cliente
        valor_parcela_sim = margem if margem > 0 else None

        # Monta lista de matrículas para tentar (pode ter múltiplos empregos)
        matriculas_tentar = data.get("facta_matriculas") or ([matricula] if matricula else [])

        simulacoes = []
        matricula_usada = matricula
        for mat in (matriculas_tentar or [None]):
            try:
                simulacoes = await asyncio.to_thread(
                    buscar_simulacoes, token, cpf, data_nasc, renda, 24, valor_parcela_sim, mat
                )
                if simulacoes:
                    matricula_usada = mat
                    print(f"[facta] Matrícula {mat} funcionou → {len(simulacoes)} simulações", flush=True)
                    break
            except MatriculaRequeridaError as e:
                print(f"[facta] Matrícula requerida pela API, tentando: {e.matriculas}", flush=True)
                for mat2 in e.matriculas:
                    try:
                        simulacoes = await asyncio.to_thread(
                            buscar_simulacoes, token, cpf, data_nasc, renda, 24, valor_parcela_sim, mat2
                        )
                        if simulacoes:
                            matricula_usada = mat2
                            print(f"[facta] Matrícula {mat2} funcionou", flush=True)
                            break
                    except Exception as e3:
                        print(f"[facta] Matrícula {mat2} falhou: {e3}", flush=True)
                if simulacoes:
                    break
            except Exception as e2:
                print(f"[facta] Matrícula {mat} falhou: {e2}", flush=True)

        matricula = matricula_usada or matricula
        print(f"[facta] {len(simulacoes)} simulacoes encontradas (matricula={matricula})", flush=True)

        if not simulacoes:
            return (
                "Infelizmente não encontrei opções de crédito disponíveis para o seu perfil no momento.\n\n"
                "Isso pode ocorrer quando a margem disponível está comprometida com outros descontos. "
                "Caso sua situação mude, pode nos chamar novamente! 😊"
            ), State.GREETING, {}

        # Prefere 18 parcelas; se não disponível, usa a primeira (já ordenada por preferência)
        melhor = next((s for s in simulacoes if int(s.get("prazo", 0)) == 18), simulacoes[0])
        valor_parcela = float(melhor.get("parcela") or melhor.get("valorParcela") or 0)
        valor_liberado = float(melhor.get("valor_liquido") or melhor.get("valorLiquido") or melhor.get("valorLiberado") or 0)
        num_parcelas = int(melhor.get("prazo") or 24)

        reply = (
            f"Simulação realizada! 🎉\n\n"
            f"💰 Valor liberado: *{_brl(valor_liberado)}*\n"
            f"📅 Parcelas: {num_parcelas}x de {_brl(valor_parcela)}\n\n"
            f"Podemos prosseguir ou deseja ver outros prazos? 😊"
        )

        return reply, State.FACTA_CONFIRM_SIMULATION, {
            **data,
            "facta_token": token,
            "facta_matricula": matricula,
            "facta_simulacoes": simulacoes,
            "facta_sim_selecionada": melhor,
            "facta_sim_inicial": melhor,
            "facta_nome": nome,
            "facta_data_nasc": data_nasc,
            "facta_renda": renda,
            "facta_sexo": sexo,
            "facta_nome_mae": nome_mae,
            "facta_margem": margem,
        }

    except ValueError as e:
        msg = str(e).lower()
        print(f"[_rodar_simulacao_facta] {e}", flush=True)
        if "nenhuma tabela" in msg or "sem tabela" in msg or "nao encontrou" in msg:
            return (
                "Infelizmente não encontrei opções de crédito disponíveis para o seu perfil no momento.\n\n"
                "Isso pode ocorrer quando a margem disponível está comprometida com outros descontos. "
                "Caso sua situação mude, pode nos chamar novamente! 😊"
            ), State.GREETING, {}
        return "Não consegui simular no momento. Tente novamente em instantes. 🙏", State.WAITING_CPF, {}
    except Exception as e:
        import traceback
        print(f"[_rodar_simulacao_facta] Erro: {e}", flush=True)
        try:
            print(traceback.format_exc().encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
        return "Não consegui simular no momento. Tente novamente em instantes. 🙏", State.WAITING_CPF, {}


async def _rodar_simulacao_facta_custom(user: dict, data: dict, valor_parcela: float, num_parcelas: int) -> tuple:
    """Re-simula Facta com prazo e parcela personalizados."""
    try:
        token = await asyncio.to_thread(get_facta_token)
        cpf = user.get("cpf") or data.get("cpf", "")
        data_nasc = data.get("facta_data_nasc", "")
        renda = float(data.get("facta_renda") or 0.0)
        matriculas = data.get("facta_matriculas") or ([data.get("facta_matricula")] if data.get("facta_matricula") else [None])

        simulacoes = []
        for mat in matriculas:
            try:
                simulacoes = await asyncio.to_thread(
                    buscar_simulacoes, token, cpf, data_nasc, renda, num_parcelas, valor_parcela, mat
                )
                if simulacoes:
                    print(f"[facta_custom] Matrícula {mat} funcionou", flush=True)
                    break
            except MatriculaRequeridaError as e:
                for mat2 in e.matriculas:
                    try:
                        simulacoes = await asyncio.to_thread(
                            buscar_simulacoes, token, cpf, data_nasc, renda, num_parcelas, valor_parcela, mat2
                        )
                        if simulacoes:
                            print(f"[facta_custom] Matrícula {mat2} funcionou", flush=True)
                            break
                    except Exception:
                        pass
                if simulacoes:
                    break
            except Exception as e2:
                print(f"[facta_custom] Matrícula {mat} falhou: {e2}", flush=True)

        if not simulacoes:
            return (
                "Esse prazo não está disponível. Por favor, informe outro prazo: 12x, 18x, 24x ou 36x."
            ), State.FACTA_WAITING_CUSTOM_TERM, {}

        melhor = simulacoes[0]
        vp = float(melhor.get("parcela") or melhor.get("valorParcela") or valor_parcela)
        vl = float(melhor.get("valor_liquido") or melhor.get("valorLiquido") or melhor.get("valorLiberado") or 0)
        prazo = int(melhor.get("prazo") or num_parcelas)

        reply = (
            f"Simulação realizada! 🎉\n\n"
            f"💰 Valor liberado: *{_brl(vl)}*\n"
            f"📅 Parcelas: {prazo}x de {_brl(vp)}\n\n"
            f"Podemos prosseguir ou deseja ver outros prazos? 😊"
        )

        return reply, State.FACTA_CONFIRM_SIMULATION, {
            **data,
            "facta_token": token,
            "facta_sim_selecionada": melhor,
            "facta_simulacoes": simulacoes,
        }

    except Exception as e:
        print(f"[_rodar_simulacao_facta_custom] Erro: {e}", flush=True)
        return (
            "Esse prazo não está disponível. Por favor, informe outro prazo: 12x, 18x, 24x ou 36x."
        ), State.FACTA_WAITING_CUSTOM_TERM, {}


async def _gerar_proposta_facta(user: dict, data: dict) -> tuple:
    """Grava simulação, dados pessoais e digita proposta na Facta."""
    try:
        # Garante token fresco
        token = await asyncio.to_thread(get_facta_token)

        cpf = user.get("cpf") or data.get("facta_cpf", "")
        matricula = data.get("facta_matricula", "")
        sim = data.get("facta_sim_selecionada", {})

        # Calcula parcela e operação consistentes
        margem = float(data.get("facta_margem") or 0)
        coeficiente = float(sim.get("coeficiente") or 0)
        parcela_sim = float(sim.get("parcela") or sim.get("valorParcela") or 0)

        # Usa a parcela da simulação; se exceder a margem, usa a margem
        if margem > 0 and parcela_sim > margem:
            valor_parcela_gravar = margem
        else:
            valor_parcela_gravar = parcela_sim if parcela_sim > 0 else margem

        # Recalcula valor_operacao consistente com a parcela real
        if coeficiente > 0 and valor_parcela_gravar > 0:
            valor_operacao = round(valor_parcela_gravar / coeficiente, 2)
        else:
            valor_operacao = float(sim.get("contrato") or sim.get("valorOperacao") or 0)

        print(f"[facta] etapa1: parcela={valor_parcela_gravar} operacao={valor_operacao} coef={coeficiente}", flush=True)

        # Etapa 1: grava simulação
        resultado_sim = await asyncio.to_thread(
            gravar_simulacao,
            token,
            cpf,
            data.get("facta_data_nasc", ""),
            str(sim.get("codigoTabela") or sim.get("tabela") or ""),
            valor_operacao,
            coeficiente,
            int(sim.get("prazo") or 24),
            valor_parcela_gravar,
            matricula,
        )
        # Se retornar prestacao_maxima, ajusta parcela e recalcula operação
        if isinstance(resultado_sim, dict) and resultado_sim.get("erro"):
            prestacao_max = resultado_sim.get("prestacao_maxima")
            if prestacao_max:
                vp2 = float(prestacao_max)
                vo2 = round(vp2 / coeficiente, 2) if coeficiente > 0 else valor_operacao
                print(f"[facta] etapa1 retry: prestacao_maxima={vp2} operacao={vo2}", flush=True)
                resultado_sim = await asyncio.to_thread(
                    gravar_simulacao,
                    token, cpf,
                    data.get("facta_data_nasc", ""),
                    str(sim.get("codigoTabela") or sim.get("tabela") or ""),
                    vo2, coeficiente,
                    int(sim.get("prazo") or 24),
                    vp2, matricula,
                )
            if resultado_sim.get("erro"):
                raise ValueError(f"Facta etapa1: {resultado_sim.get('mensagem')}")

        id_simulador = str(resultado_sim.get("id_simulador") or resultado_sim.get("simulador") or resultado_sim.get("id") or "")
        print(f"[facta] id_simulador={id_simulador} | resposta={resultado_sim}", flush=True)

        # Busca código da cidade
        estado = data.get("address_state", "SP")
        cidade = data.get("address_city", "")
        codigo_cidade = await asyncio.to_thread(buscar_codigo_cidade, token, estado, cidade)
        if not codigo_cidade:
            codigo_cidade = "1"

        # Celular no formato Facta
        celular_override = data.get("facta_celular_override")
        if celular_override:
            celular_facta = celular_override
        else:
            celular_facta = _formatar_celular_facta(user.get("phone", ""))

        # RG: últimos 8 dígitos do CPF
        cpf_digits = re.sub(r"\D", "", cpf)
        rg = data.get("rg_number") or (cpf_digits[3:] if len(cpf_digits) >= 8 else cpf_digits)

        pix_key = data.get("pix_key", "")
        tipo_pix = _detectar_tipo_pix_inteligente(
            pix_key,
            cpf=user.get("cpf") or data.get("cpf", ""),
            phone=user.get("phone", ""),
        )
        # Chave PIX telefone → garante formato +55DDDNUMERO
        if tipo_pix == "2":
            digits = re.sub(r"\D", "", pix_key)
            if not digits.startswith("55"):
                digits = "55" + digits
            pix_key = "+" + digits

        # Etapa 2: dados pessoais
        resultado_pessoal = await asyncio.to_thread(
            gravar_dados_pessoais,
            token,
            id_simulador,
            cpf,
            data.get("facta_nome") or user.get("name", ""),
            data.get("facta_sexo", "M"),
            data.get("facta_data_nasc", ""),
            rg,
            estado,
            data.get("facta_nome_mae", "NAO DECLARADO"),
            celular_facta,
            float(data.get("facta_renda") or 0),
            data.get("address_cep", ""),
            data.get("address_street", ""),
            data.get("address_number", "01"),
            data.get("address_neighborhood", ""),
            estado,
            codigo_cidade,
            matricula,
            pix_key,
            tipo_pix,
        )
        if isinstance(resultado_pessoal, dict) and resultado_pessoal.get("erro"):
            raise ValueError(f"Facta etapa2: {resultado_pessoal.get('mensagem')}")
        codigo_cliente = str(
            resultado_pessoal.get("codigo_cliente") or
            resultado_pessoal.get("id_cliente") or
            resultado_pessoal.get("cliente") or
            resultado_pessoal.get("codigo") or
            resultado_pessoal.get("id") or ""
        )
        print(f"[facta] codigo_cliente={codigo_cliente} | resposta={resultado_pessoal}", flush=True)

        # Etapa 3: digita proposta
        resultado_proposta = await asyncio.to_thread(
            digitar_proposta, token, codigo_cliente, id_simulador
        )
        print(f"[facta] proposta: {str(resultado_proposta)[:120]}", flush=True)

        link = (
            resultado_proposta.get("url_formalizacao") or
            resultado_proposta.get("url_assinatura") or
            resultado_proposta.get("link_assinatura") or
            resultado_proposta.get("url") or
            resultado_proposta.get("link") or ""
        )

        reply = (
            f"Proposta gerada com sucesso!\n\n"
            f"Segue o link para assinatura digital:\n{link}\n\n"
            f"A assinatura é 100% digital e segura — após assinar, o valor será creditado via PIX.\n\n"
            f"Qualquer dúvida, é só me chamar!"
        )
        return reply, State.PROPOSAL_SENT, {"formalization_url": link}

    except Exception as e:
        import traceback
        print(f"[_gerar_proposta_facta] Erro: {e}", flush=True)
        try:
            print(traceback.format_exc().encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
        err_str = str(e).lower()
        if "telefone" in err_str and ("cpf" in err_str or "outro" in err_str):
            return (
                "Houve um conflito com o telefone cadastrado. Por favor, informe outro número de celular com DDD:"
            ), State.FACTA_WAITING_PHONE, {}
        return "Erro ao gerar proposta. Por favor, entre em contato com nosso atendimento. 🙏", State.ERROR, {}


def _parse_date(text: str) -> str | None:
    parts = re.findall(r"\d+", text)
    if len(parts) == 3:
        d, m, y = parts[0], parts[1], parts[2]
        if len(y) == 4:
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return None


def _detectar_tipo_pix(key: str) -> str:
    key = key.strip()
    if re.match(r"^\d{11}$", re.sub(r"\D", "", key)):
        return "cpf"
    if "@" in key:
        return "email"
    if re.match(r"^\d{10,11}$", re.sub(r"\D", "", key)):
        return "phone"
    return "random"
