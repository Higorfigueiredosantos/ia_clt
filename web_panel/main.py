import os
import sys
import time
import uuid
import asyncio
import requests
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ─────────────────────────────────────────────────────────────────

V8_AUTH_URL  = os.getenv("V8_AUTH_URL", "https://auth.v8sistema.com/oauth/token")
V8_CLIENT_ID = os.getenv("V8_CLIENT_ID", "")
V8_USERNAME  = os.getenv("V8_USERNAME", "")
V8_PASSWORD  = os.getenv("V8_PASSWORD", "")
V8_BASE_URL  = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io"
V8_CONFIG_ID = os.getenv("V8_CONFIG_ID", "fbbb3a06-05ca-4567-9a92-ce78cb4db796")
PANEL_USER   = os.getenv("PANEL_USER", "admin")
PANEL_PASS   = os.getenv("PANEL_PASS", "clt2024")

# ─── V8 AUTH ─────────────────────────────────────────────────────────────────

_token_cache = {"token": None, "expires_at": 0}

def v8_get_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]
    payload = {
        "grant_type": "password",
        "audience": "https://bff.v8sistema.com",
        "scope": "offline_access",
        "client_id": V8_CLIENT_ID,
        "username": V8_USERNAME,
        "password": V8_PASSWORD,
    }
    r = requests.post(V8_AUTH_URL, data=payload, timeout=30)
    r.raise_for_status()
    token = r.json()["access_token"]
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + 14 * 60
    return token

def v8_headers():
    return {"Authorization": f"Bearer {v8_get_token()}"}

# ─── V8 CONSULTA (sync – roda em executor) ───────────────────────────────────

_STATUS_REJEITADO = {"REJECTED", "DENIED", "FAILED", "ERROR", "CANCELLED", "EXPIRED"}

def _buscar_dados_consulta(consult_id: str) -> dict:
    r = requests.get(f"{V8_BASE_URL}/private-consignment/consult/{consult_id}",
                     headers=v8_headers(), timeout=30)
    r.raise_for_status()
    return r.json()

def _cpf_limpo(cpf: str) -> str:
    return "".join(c for c in cpf if c.isdigit()).zfill(11)

def _cpf_da_consulta(dados: dict) -> str:
    """Extrai CPF dos dados da consulta, testando vários campos possíveis."""
    raw = (
        dados.get("borrowerDocumentNumber") or
        dados.get("documentNumber") or
        dados.get("cpf") or
        (dados.get("borrower") or {}).get("documentNumber") or
        (dados.get("borrower") or {}).get("cpf") or ""
    )
    return "".join(c for c in str(raw) if c.isdigit()).zfill(11)

def _gerar_consulta_nova(cpf, nome, email, celular, data_nasc, genero) -> str:
    """Cria SEMPRE uma nova consulta. Retorna consult_id."""
    cpf_ok = _cpf_limpo(cpf)
    area_code = celular[:2]
    number    = celular[2:]
    if len(number) == 8:
        number = "9" + number

    payload = {
        "borrowerDocumentNumber": cpf_ok,
        "signerName": nome,
        "signerEmail": email,
        "signerPhone": {"countryCode": "55", "areaCode": area_code, "phoneNumber": number},
        "birthDate": data_nasc,
        "gender": {"M": "male", "F": "female", "m": "male", "f": "female"}.get(genero, genero),
    }
    r = requests.post(f"{V8_BASE_URL}/private-consignment/consult",
                      headers=v8_headers(), json=payload, timeout=30)

    if r.ok:
        return r.json()["id"]

    # Trata erro de consulta já existente
    body = {}
    try: body = r.json()
    except: pass

    erro_msg = str(body) if body else r.text[:300]
    print(f"[gerar_consulta] erro {r.status_code}: {erro_msg}", flush=True)

    # Tenta extrair consult_id da resposta de erro
    consult_id_erro = (
        body.get("consultId") or body.get("consult_id") or
        body.get("id") or (body.get("data") or {}).get("consultId") or
        (body.get("data") or {}).get("id") or ""
    )
    if consult_id_erro:
        print(f"[gerar_consulta] usando consult_id do erro: {consult_id_erro}", flush=True)
        return consult_id_erro

    # Fallback: busca na lista filtrando ESTRITAMENTE por CPF
    consult_id = _buscar_consulta_por_cpf(cpf_ok)
    if consult_id:
        print(f"[gerar_consulta] reutilizando consulta existente do CPF {cpf_ok}: {consult_id}", flush=True)
        return consult_id

    # Mostra erro real da V8 para diagnóstico
    raise Exception(f"V8 API erro {r.status_code}: {erro_msg}")

def _buscar_consulta_por_cpf(cpf_ok: str) -> Optional[str]:
    """Busca consulta existente filtrando ESTRITAMENTE pelo CPF informado."""
    r = requests.get(
        f"{V8_BASE_URL}/private-consignment/consult",
        headers=v8_headers(),
        params={"limit": 20, "page": 1, "documentNumber": cpf_ok},
        timeout=30,
    )
    if not r.ok:
        return None

    for item in r.json().get("data", []):
        cid = item["id"]
        try:
            chk = requests.get(
                f"{V8_BASE_URL}/private-consignment/consult/{cid}",
                headers=v8_headers(), timeout=30
            )
            if not chk.ok:
                continue
            detalhe = chk.json()
            status  = detalhe.get("status", "")

            # VALIDA CPF: ignora consultas de outros clientes
            cpf_detalhe = _cpf_da_consulta(detalhe)
            if cpf_detalhe and cpf_detalhe != cpf_ok:
                print(f"[buscar_consulta] IGNORANDO {cid}: CPF diferente ({cpf_detalhe} != {cpf_ok})", flush=True)
                continue

            # Só reutiliza se ainda processável
            if status in ("WAITING_CONSENT", "WAITING_CONSULT", "AUTHORIZED", "IN_ANALYSIS", "PENDING", "PROCESSING", "CREATED", "SUCCESS"):
                # Se SUCCESS, verifica se tem margem válida
                if status == "SUCCESS":
                    margem = float(detalhe.get("marginBaseValue") or 0)
                    if margem <= 0:
                        print(f"[buscar_consulta] IGNORANDO {cid}: SUCCESS mas margem=0", flush=True)
                        continue
                print(f"[buscar_consulta] usando {cid} status={status} cpf={cpf_detalhe}", flush=True)
                return cid
        except Exception as e:
            print(f"[buscar_consulta] erro ao verificar {cid}: {e}", flush=True)
    return None

def _fluxo_consulta_sync(task_id: str, cpf, nome, email, celular, data_nasc, genero):
    cpf_ok = _cpf_limpo(cpf)
    tasks[task_id] = {"status": "processing", "step": "Gerando consulta...", "data": None, "error": None}
    try:
        print(f"[fluxo] iniciando para CPF={cpf_ok} nome={nome}", flush=True)
        consult_id = _gerar_consulta_nova(cpf, nome, email, celular, data_nasc, genero)
        tasks[task_id]["step"] = "Verificando status da consulta..."

        # Verifica status atual antes de autorizar
        dados_pre = _buscar_dados_consulta(consult_id)
        status_pre = dados_pre.get("status", "")
        print(f"[fluxo] consult_id={consult_id} status_pre={status_pre}", flush=True)

        # Valida que a consulta é do CPF correto
        cpf_consulta = _cpf_da_consulta(dados_pre)
        if cpf_consulta and cpf_consulta != cpf_ok:
            print(f"[fluxo] ERRO: consulta {consult_id} é do CPF {cpf_consulta}, não {cpf_ok}", flush=True)
            tasks[task_id] = {"status": "error", "data": None,
                              "error": f"Consulta retornou dados de outro cliente. Tente novamente.",
                              "step": "Erro de validação"}
            return

        # Se já tem resultado com margem válida, retorna direto
        if status_pre == "SUCCESS":
            margem = float(dados_pre.get("marginBaseValue") or 0)
            if margem > 0:
                print(f"[fluxo] SUCCESS com margem={margem}, retornando direto", flush=True)
                _montar_resultado(task_id, consult_id, dados_pre)
                return

        if status_pre.upper() in _STATUS_REJEITADO:
            tasks[task_id] = {"status": "error", "step": "Rejeitado", "data": None,
                              "error": f"Consulta rejeitada com status: {status_pre}"}
            return

        # Autoriza se necessário
        if status_pre not in ("WAITING_CONSULT", "SUCCESS"):
            tasks[task_id]["step"] = "Autorizando consulta..."
            try:
                requests.post(
                    f"{V8_BASE_URL}/private-consignment/consult/{consult_id}/authorize",
                    headers=v8_headers(), timeout=30
                )
                print(f"[fluxo] consulta autorizada", flush=True)
            except Exception as e:
                print(f"[fluxo] erro ao autorizar: {e}", flush=True)

        tasks[task_id]["step"] = "Aguardando processamento (pode levar até 3 min)..."

        # Polling com esperas progressivas
        esperas = [30, 50, 50, 40]
        dados = None
        for i, espera in enumerate(esperas):
            print(f"[fluxo] aguardando {espera}s antes da tentativa {i+1}/4...", flush=True)
            time.sleep(espera)
            try:
                dados = _buscar_dados_consulta(consult_id)
                status = dados.get("status", "")
                margem = float(dados.get("marginBaseValue") or 0)
                tasks[task_id]["step"] = f"Processando... tentativa {i+1}/4 (status: {status})"
                print(f"[fluxo] tentativa {i+1}/4: status={status} margem={margem}", flush=True)

                if status == "SUCCESS" and margem > 0:
                    break
                if status.upper() in _STATUS_REJEITADO:
                    tasks[task_id] = {"status": "error", "step": "Rejeitado", "data": None,
                                      "error": f"Consulta rejeitada: {status}"}
                    return
            except Exception as e:
                print(f"[fluxo] tentativa {i+1}/4 erro: {e}", flush=True)
                tasks[task_id]["step"] = f"Tentativa {i+1}/4 com erro, aguardando..."

        # Verifica resultado final
        status_final = dados.get("status") if dados else "desconhecido"
        margem_final = float(dados.get("marginBaseValue") or 0) if dados else 0

        if not dados or status_final != "SUCCESS" or margem_final <= 0:
            print(f"[fluxo] timeout: status={status_final} margem={margem_final}", flush=True)
            tasks[task_id] = {"status": "error", "step": "Timeout", "data": None,
                              "error": f"Consulta não retornou margem válida (status: {status_final}). Tente novamente."}
            return

        print(f"[fluxo] SUCESSO: margem={margem_final}", flush=True)
        _montar_resultado(task_id, consult_id, dados)

    except Exception as e:
        print(f"[fluxo] exceção: {e}", flush=True)
        tasks[task_id] = {"status": "error", "step": "Erro", "data": None, "error": str(e)}


def _montar_resultado(task_id, consult_id, dados):
    tasks[task_id] = {
        "status": "success",
        "step": "Concluído",
        "error": None,
        "data": {
            "consult_id": consult_id,
            "margem_disponivel": float(dados.get("marginBaseValue") or 0),
            "simulation_limit": dados.get("simulationLimit", {}),
            "employer_name": dados.get("employerName", ""),
            "employer_document_number": dados.get("employerDocumentNumber", ""),
            "registration_number": dados.get("registrationNumber", ""),
            "admission_date": dados.get("admissionDate", ""),
            "mother_name": dados.get("motherName", ""),
            "recommended_installment_value": float(dados.get("recommendedSimulationInstallmentValue") or 0),
            "recommended_installment_number": dados.get("recommendedSimulationInstallmentNumber"),
            "gender": dados.get("gender", ""),
        }
    }

# ─── APP ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Painel CLT")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

executor = ThreadPoolExecutor(max_workers=10)
tasks: dict[str, dict] = {}  # task_id → estado da consulta

# Serve o index.html
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")

@app.get("/")
def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

# ─── AUTH ─────────────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
def login(body: LoginBody):
    if body.username == PANEL_USER and body.password == PANEL_PASS:
        return {"ok": True, "token": "panel-ok"}
    raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")

# ─── DADOS CLIENTE ────────────────────────────────────────────────────────────

@app.get("/api/v8/dados-cliente/{cpf}")
def dados_cliente(cpf: str):
    cpf_limpo = "".join(c for c in cpf if c.isdigit()).zfill(11)
    try:
        r = requests.get(
            f"{V8_BASE_URL}/private-consignment/consult/client-data/basic/{cpf_limpo}",
            headers=v8_headers(), timeout=15)
        if r.ok:
            return r.json()
        return {}
    except:
        return {}

# ─── CONSULTA (async) ────────────────────────────────────────────────────────

class ConsultarBody(BaseModel):
    cpf: str
    nome: str
    email: str
    celular: str
    data_nasc: str   # YYYY-MM-DD
    genero: str      # M / F

@app.post("/api/v8/consultar")
async def consultar(body: ConsultarBody, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "processing", "step": "Iniciando...", "data": None, "error": None}
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        executor,
        _fluxo_consulta_sync,
        task_id, body.cpf, body.nome, body.email,
        body.celular, body.data_nasc, body.genero
    )
    return {"task_id": task_id}

@app.get("/api/v8/consulta/{task_id}")
def consulta_status(task_id: str):
    estado = tasks.get(task_id)
    if not estado:
        raise HTTPException(status_code=404, detail="Task não encontrada")
    return estado

# ─── PRÉ-CALCULAR PARCELAS ───────────────────────────────────────────────────

class ParcelajBody(BaseModel):
    consult_id: str

@app.post("/api/v8/parcelas")
def parcelas(body: ParcelajBody):
    payload = {"config_id": V8_CONFIG_ID, "consult_id": body.consult_id}
    r = requests.post(
        f"{V8_BASE_URL}/private-consignment/simulation/pre-calculate-installments",
        headers=v8_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

# ─── SIMULAÇÃO ───────────────────────────────────────────────────────────────

class SimularBody(BaseModel):
    consult_id: str
    installment_face_value: float
    number_of_installments: int

@app.post("/api/v8/simular")
def simular(body: SimularBody):
    payload = {
        "config_id": V8_CONFIG_ID,
        "consult_id": body.consult_id,
        "installment_face_value": body.installment_face_value,
        "number_of_installments": body.number_of_installments,
    }
    r = requests.post(
        f"{V8_BASE_URL}/private-consignment/simulation",
        headers=v8_headers(), json=payload, timeout=30)
    if not r.ok:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    data = r.json()
    return {
        "simulation_id": data["id_simulation"],
        "installment_value": data["installment_value"],
        "number_of_installments": data["number_of_installments"],
        "operation_amount": data["operation_amount"],
        "disbursement_amount": data["disbursement_amount"],
        "iof_amount": data["iof_amount"],
        "monthly_interest_rate": data["monthly_interest_rate"],
        "first_installment_date": data["first_installment_date"],
    }

# ─── PROPOSTA ────────────────────────────────────────────────────────────────

class PropostaBody(BaseModel):
    simulation_id: str
    name: str
    email: str
    cpf: str
    phone_area: str
    phone_number: str
    birth_date: str
    mother_name: str
    gender: str
    marital_status: str
    nationality: str
    rg: str
    rg_date: str
    rg_orgao: str
    postal_code: str
    street: str
    number: str
    complement: str
    neighborhood: str
    city: str
    state: str
    pix_key_type: str
    pix_key: str
    bank_id: str = ""
    employer_name: str
    employer_document_number: str
    registration_number: str

@app.post("/api/v8/proposta")
def proposta(body: PropostaBody):
    cpf_limpo = "".join(c for c in body.cpf if c.isdigit()).zfill(11)
    phone_num = "".join(c for c in body.phone_number if c.isdigit())
    cep_limpo = "".join(c for c in body.postal_code if c.isdigit())

    # Mapeia código numérico para string exigida pela V8
    _PIX_TYPE_MAP = {
        "1": "cpf", "2": "telefone", "3": "e-mail", "4": "chave aleatória",
        "cpf": "cpf", "telefone": "telefone", "e-mail": "e-mail", "chave aleatória": "chave aleatória",
    }
    pix_type = _PIX_TYPE_MAP.get(body.pix_key_type, "cpf")

    # Normaliza chave PIX conforme tipo
    pix_key = body.pix_key
    if pix_type == "cpf":
        pix_key = "".join(c for c in pix_key if c.isdigit())
    elif pix_type == "telefone":
        digits = "".join(c for c in pix_key if c.isdigit())
        if not digits.startswith("55"):
            digits = "55" + digits
        pix_key = "+" + digits

    bank_obj = {
        "transfer_method": "pix",
        "pix_key": pix_key,
        "pix_key_type": pix_type,
    }
    if body.bank_id:
        bank_obj["bank_id"] = body.bank_id

    payload = {
        "borrower": {
            "name": body.name,
            "email": body.email,
            "phone": {"country_code": "55", "area_code": body.phone_area, "number": phone_num},
            "political_exposition": False,
            "address": {
                "postal_code": cep_limpo,
                "city": body.city,
                "state": body.state.upper(),
                "number": body.number,
                "street": body.street,
                "complement": body.complement or "",
                "neighborhood": body.neighborhood,
            },
            "birth_date": body.birth_date,
            "mother_name": body.mother_name,
            "nationality": body.nationality or "Brasileiro",
            "gender": {"M": "male", "F": "female", "m": "male", "f": "female"}.get(body.gender, body.gender),
            "person_type": "natural",
            "marital_status": body.marital_status,
            "individual_document_number": cpf_limpo,
            "document_identification_date": body.rg_date,
            "document_issuer": body.rg_orgao or "SSP",
            "document_identification_type": "rg",
            "document_identification_number": body.rg,
            "bank": bank_obj,
            "work_data": {
                "employer_name": body.employer_name,
                "employer_document_number": body.employer_document_number,
                "registration_number": body.registration_number,
            },
        },
        "simulation_id": body.simulation_id,
    }

    r = requests.post(
        f"{V8_BASE_URL}/private-consignment/operation",
        headers=v8_headers(), json=payload, timeout=30)
    if not r.ok:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    data = r.json()
    return {"operation_id": data["id"], "formalization_url": data["formalization_url"]}

# ─── LISTAR OPERAÇÕES ────────────────────────────────────────────────────────

V8_BFF_URL = "https://bff.v8sistema.com"

@app.get("/api/v8/operacoes")
def listar_operacoes(
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    status: Optional[str] = None,
):
    params: dict = {"page": page, "limit": limit}
    if startDate: params["startDate"] = startDate
    if endDate:   params["endDate"]   = endDate
    if status:    params["status"]    = status
    r = requests.get(
        f"{V8_BFF_URL}/private-consignment/operation",
        headers=v8_headers(), params=params, timeout=30
    )
    if not r.ok:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()

# ─── VIA CEP ─────────────────────────────────────────────────────────────────

@app.get("/api/viacep/{cep}")
def viacep(cep: str):
    cep_limpo = "".join(c for c in cep if c.isdigit())
    r = requests.get(f"https://viacep.com.br/ws/{cep_limpo}/json/", timeout=10)
    if r.ok:
        return r.json()
    raise HTTPException(status_code=404, detail="CEP não encontrado")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PANEL_PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
