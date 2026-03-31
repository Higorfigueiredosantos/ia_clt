import asyncio
from fastapi import APIRouter, Request, Response
from app.services.ai_agent import handle_message

router = APIRouter()

# Canal habilitado para IA
_CANAL_HABILITADO = "ia_clt"

_processed_ids: set = set()  # deduplicação simples


@router.get("")
async def verify_webhook(request: Request):
    """Endpoint de verificação (mantido para compatibilidade)."""
    return Response(content="ok", media_type="text/plain")


@router.post("")
async def receive_message(request: Request):
    """Recebe mensagens do CRM. Responde 200 imediatamente."""
    body = await request.json()
    asyncio.create_task(_process_webhook(body))
    return {"status": "ok"}


async def _process_webhook(body: dict):
    try:
        event = body.get("event", "")
        direction = body.get("direction", "")

        # Só processa mensagens recebidas
        if event != "message_created" or direction != "received":
            return

        # Filtra canal habilitado
        channel_name = body.get("channel", {}).get("name", "")
        if channel_name != _CANAL_HABILITADO:
            print(f"[webhook] canal ignorado: {channel_name!r}", flush=True)
            return

        # Filtra automação ativa — se false, IA está desligada para esse contato
        automation_active = body.get("automation_active", False)
        if not automation_active:
            print(f"[webhook] automation_active=false, ignorando contato {body.get('contact_phone')}", flush=True)
            return

        msg = body.get("message", {})
        msg_type = msg.get("type", "text")

        # Tipos suportados: text, audio, image
        if msg_type not in ("text", "audio", "image"):
            print(f"[webhook] ignorando tipo={msg_type}", flush=True)
            return

        message_id = msg.get("whatsapp_message_id", "") or body.get("conversation_id", "")
        text = msg.get("content", "").strip()
        media_url = msg.get("media_url", "")
        media_type = msg.get("media_type", "")
        phone = body.get("contact_phone", "")
        name = body.get("contact_name", "")
        conversation_id = body.get("conversation_id", "")
        channel_id = body.get("channel", {}).get("id", "")
        contact_id = (
            body.get("contact_id") or
            body.get("contact", {}).get("id", "") or ""
        )

        if not phone:
            return
        # Mensagem de texto vazia e sem mídia → ignora
        if not text and not media_url:
            return

        print(f"[webhook] msg_id={message_id} dup={message_id in _processed_ids}", flush=True)

        # Deduplicação
        if message_id and message_id in _processed_ids:
            return
        if message_id:
            _processed_ids.add(message_id)
        if len(_processed_ids) > 1000:
            _processed_ids.clear()

        print(f"[webhook] chamando handle_message phone={phone} type={msg_type} text={text[:30]!r}", flush=True)

        await handle_message(phone, name, text, message_id,
                             crm_conversation_id=conversation_id,
                             crm_channel_id=channel_id,
                             crm_contact_id=contact_id,
                             media_url=media_url,
                             media_type=media_type)
        print(f"[webhook] handle_message concluído", flush=True)

    except Exception as e:
        import traceback
        print(f"[webhook] Erro ao processar: {e}", flush=True)
        try:
            print(traceback.format_exc().encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
