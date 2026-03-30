import re
import time
import requests
from tools.v8.auth import get_token

BASE_URL = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io"


class ConsultaEmAnaliseError(Exception):
    """Levantado quando a consulta não atingiu SUCCESS após 3 tentativas. Inclui o consult_id para retry."""
    def __init__(self, consult_id: str):
        super().__init__(f"Consulta {consult_id} ainda em análise")
        self.consult_id = consult_id


class ConsultaRejeitadaError(Exception):
    """Levantado quando a consulta retorna status de rejeição (REJECTED, DENIED, etc)."""
    def __init__(self, consult_id: str, status: str):
        super().__init__(f"Consulta {consult_id} rejeitada com status={status}")
        self.consult_id = consult_id
        self.status = status


_STATUS_REJEITADO = {"REJECTED", "DENIED", "FAILED", "ERROR", "CANCELLED", "EXPIRED"}
_STATUS_PENDENTE  = {"WAITING_CONSENT", "WAITING_CONSULT", "AUTHORIZED", "IN_ANALYSIS", "PENDING", "PROCESSING", "CREATED"}


def _headers():
    return {"Authorization": f"Bearer {get_token()}"}


def buscar_consulta_ativa(cpf: str) -> str | None:
    """Busca consulta ativa existente para o CPF. Retorna consult_id ou None.
    Valida cada consulta individualmente para evitar reutilizar consultas expiradas/inválidas."""
    cpf_limpo = cpf.replace(".", "").replace("-", "").zfill(11)
    response = requests.get(
        f"{BASE_URL}/private-consignment/consult",
        headers=_headers(),
        params={"limit": 10, "page": 1, "documentNumber": cpf_limpo},
    )
    if not response.ok:
        return None
    data = response.json().get("data", [])
    for consulta in data:
        consult_id = consulta["id"]
        # Tenta verificar CPF direto no item da lista
        list_cpf = re.sub(r"\D", "", str(
            consulta.get("borrowerDocumentNumber") or
            consulta.get("documentNumber") or
            consulta.get("cpf") or ""
        ))
        print(f"[buscar_consulta_ativa] lista: {consult_id} cpf_lista={list_cpf} esperado={cpf_limpo} keys={list(consulta.keys())}")
        if list_cpf and list_cpf != cpf_limpo:
            print(f"[buscar_consulta_ativa] IGNORANDO lista — CPF diferente ({list_cpf})")
            continue
        # Verifica se a consulta ainda é acessível
        try:
            check = requests.get(
                f"{BASE_URL}/private-consignment/consult/{consult_id}",
                headers=_headers(),
            )
            if check.ok:
                consult_data = check.json()
                status = consult_data.get("status", "")
                # Tenta verificar CPF no detalhe (vários nomes possíveis)
                detail_cpf = re.sub(r"\D", "", str(
                    consult_data.get("borrowerDocumentNumber") or
                    consult_data.get("documentNumber") or
                    consult_data.get("cpf") or
                    consult_data.get("borrower", {}).get("documentNumber") or
                    consult_data.get("borrower", {}).get("cpf") or ""
                ))
                print(f"[buscar_consulta_ativa] detalhe: {consult_id} status={status} cpf_detalhe={detail_cpf} keys={list(consult_data.keys())}")
                if detail_cpf and detail_cpf != cpf_limpo:
                    print(f"[buscar_consulta_ativa] IGNORANDO detalhe — CPF diferente ({detail_cpf})")
                    continue
                # Aceita consultas que ainda podem ser processadas
                if status in ("WAITING_CONSENT", "WAITING_CONSULT", "AUTHORIZED", "SUCCESS"):
                    return consult_id
        except Exception as e:
            print(f"[buscar_consulta_ativa] Erro ao verificar {consult_id}: {e}")
    return None


def gerar_consulta(cpf: str, nome: str, email: str, celular: str, data_nasc: str, genero: str) -> str:
    """Gera consulta de margem CLT. Retorna consult_id.
    Se já existir consulta ativa, reutiliza a existente."""
    area_code = celular[:2]
    phone_number = celular[2:]
    if len(phone_number) == 8:
        phone_number = "9" + phone_number

    payload = {
        "borrowerDocumentNumber": cpf.replace(".", "").replace("-", "").zfill(11),
        "signerName": nome,
        "signerEmail": email,
        "signerPhone": {
            "countryCode": "55",
            "phoneNumber": phone_number,
            "areaCode": area_code,
        },
        "birthDate": data_nasc,
        "gender": genero,
    }

    cpf_limpo = cpf.replace(".", "").replace("-", "").zfill(11)
    response = requests.post(f"{BASE_URL}/private-consignment/consult", headers=_headers(), json=payload)
    if not response.ok:
        error_body = {}
        if response.headers.get("content-type", "").startswith("application/json"):
            error_body = response.json()
        error_type = error_body.get("type", "")
        print(f"[gerar_consulta] ERRO {response.status_code} type={error_type!r} body={str(error_body)[:300]}")
        if "consult_already_exists" in error_type or response.status_code == 400:
            # Tenta extrair consult_id diretamente da resposta de erro
            consult_id_erro = (
                error_body.get("consultId") or
                error_body.get("consult_id") or
                error_body.get("id") or
                error_body.get("data", {}).get("consultId") or
                error_body.get("data", {}).get("id") or ""
            )
            if consult_id_erro:
                print(f"[gerar_consulta] consult_id na resposta de erro: {consult_id_erro}")
                return consult_id_erro
            # Fallback: busca por CPF na lista
            consult_id = buscar_consulta_ativa(cpf)
            if consult_id:
                print(f"[gerar_consulta] Reutilizando consulta existente: {consult_id}")
                return consult_id
        print(f"[gerar_consulta] ERRO sem consult_id recuperavel")
    response.raise_for_status()
    return response.json()["id"]


def autorizar_consulta(consult_id: str) -> None:
    """Autoriza a consulta de margem."""
    response = requests.post(
        f"{BASE_URL}/private-consignment/consult/{consult_id}/authorize",
        headers=_headers(),
    )
    response.raise_for_status()


def buscar_dados_consulta(consult_id: str) -> dict:
    """Busca dados completos da consulta (margem, dados do tomador, limites de simulação)."""
    response = requests.get(
        f"{BASE_URL}/private-consignment/consult/{consult_id}",
        headers=_headers(),
    )
    response.raise_for_status()
    return response.json()


def executar_fluxo_consulta(cpf: str, nome: str, email: str, celular: str, data_nasc: str, genero: str) -> dict:
    """
    Executa fluxo completo: gerar → autorizar → buscar dados.
    Retorna dict com consult_id, margem_disponivel, dados do tomador e limites de simulação.
    """
    consult_id = gerar_consulta(cpf, nome, email, celular, data_nasc, genero)

    # Verifica status atual antes de autorizar (pode já estar em SUCCESS se reutilizando)
    dados = None
    try:
        dados_pre = buscar_dados_consulta(consult_id)
        status_pre = dados_pre.get("status", "")
        print(f"[executar_fluxo_consulta] Status inicial: {status_pre}", flush=True)
        if status_pre == "SUCCESS":
            dados = dados_pre
        elif status_pre.upper() in _STATUS_REJEITADO:
            raise ConsultaRejeitadaError(consult_id, status_pre)
        elif status_pre == "WAITING_CONSULT":
            # Já autorizada e em processamento — não tenta autorizar novamente
            print(f"[executar_fluxo_consulta] Consulta ja em processamento ({status_pre}), aguardando...", flush=True)
        else:
            autorizar_consulta(consult_id)
    except (ConsultaRejeitadaError, ConsultaEmAnaliseError):
        raise
    except Exception as e:
        print(f"[executar_fluxo_consulta] Pre-check falhou ({e}), autorizando mesmo assim...", flush=True)
        try:
            autorizar_consulta(consult_id)
        except Exception as e2:
            print(f"[executar_fluxo_consulta] Autorizacao falhou: {e2}", flush=True)

    # Aguarda 30s após autorização para a API processar
    if dados is None or dados.get("status") != "SUCCESS":
        print(f"[executar_fluxo_consulta] Aguardando 30s apos autorizacao...", flush=True)
        time.sleep(30)

    # Tenta buscar até 3 vezes (intervalo de 30s entre cada)
    if dados is None or dados.get("status") != "SUCCESS":
        dados = None
        for tentativa in range(3):
            try:
                dados = buscar_dados_consulta(consult_id)
                status_atual = dados.get("status", "")
                print(f"[executar_fluxo_consulta] Tentativa {tentativa+1}/3: status={status_atual}", flush=True)
                if status_atual == "SUCCESS":
                    break
                # Rejeição detectada → vai para Facta imediatamente
                if status_atual.upper() in _STATUS_REJEITADO:
                    raise ConsultaRejeitadaError(consult_id, status_atual)
            except (ConsultaRejeitadaError, ConsultaEmAnaliseError):
                raise
            except Exception as e:
                print(f"[executar_fluxo_consulta] Tentativa {tentativa+1}/3: erro={e}", flush=True)
            if tentativa < 2:
                time.sleep(30)

    # Se ainda não é SUCCESS, sinaliza para o agente avisar o cliente e aguardar 50s
    status_apos3 = dados.get("status") if dados else "desconhecido"
    if not dados or status_apos3 != "SUCCESS":
        print(f"[executar_fluxo_consulta] Ainda em {status_apos3}. Sinalizando para tentativa final.", flush=True)
        raise ConsultaEmAnaliseError(consult_id)

    return {
        "consult_id": consult_id,
        "margem_disponivel": float(dados.get("marginBaseValue") or 0),
        "simulation_limit": dados.get("simulationLimit", {}),
        "employer_name": dados.get("employerName"),
        "employer_document_number": dados.get("employerDocumentNumber"),
        "registration_number": dados.get("registrationNumber"),
        "admission_date": dados.get("admissionDate"),
        "mother_name": dados.get("motherName"),
        "recommended_installment_value": float(dados.get("recommendedSimulationInstallmentValue") or 0),
        "recommended_installment_number": dados.get("recommendedSimulationInstallmentNumber"),
    }
