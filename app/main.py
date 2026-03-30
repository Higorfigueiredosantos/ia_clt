import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from app.api.webhook import router as webhook_router, receive_message
from app.services.followup import iniciar_servico_followup


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicia serviço de follow-up em background
    task = asyncio.create_task(iniciar_servico_followup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="CLT IA WhatsApp", version="1.0.0", lifespan=lifespan)

app.include_router(webhook_router, prefix="/webhook")

# Armazena último erro de proposta em memória para debug
_last_proposta_error: dict = {}


def set_proposta_error(payload: dict, error_body: str):
    _last_proposta_error.clear()
    _last_proposta_error.update({"payload": payload, "error": error_body})


@app.post("/")
async def receive_message_root(request: Request):
    """CRM posta na raiz — redireciona para o handler do webhook."""
    return await receive_message(request)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/debug/proposta-error")
async def debug_proposta_error():
    return _last_proposta_error or {"msg": "Nenhum erro registrado ainda"}
