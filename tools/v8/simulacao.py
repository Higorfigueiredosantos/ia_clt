import requests
from tools.v8.auth import get_token

BASE_URL = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io"


def _headers():
    return {"Authorization": f"Bearer {get_token()}"}


def pre_calcular_installments(consult_id: str, config_id: str) -> list:
    """Retorna lista de opções de parcelas disponíveis para o cliente."""
    payload = {"config_id": config_id, "consult_id": consult_id}
    response = requests.post(
        f"{BASE_URL}/private-consignment/simulation/pre-calculate-installments",
        headers=_headers(),
        json=payload,
    )
    response.raise_for_status()
    installments = response.json().get("installments", [])
    print(f"[simulacao] pre_calcular: {len(installments)} opcoes: {[i.get('installmentNumbers') for i in installments]}", flush=True)
    return installments


def executar_simulacao(consult_id: str, config_id: str, installment_face_value: float, number_of_installments: int) -> dict:
    """Executa simulação e retorna dados financeiros completos."""
    payload = {
        "config_id": config_id,
        "consult_id": consult_id,
        "installment_face_value": installment_face_value,
        "number_of_installments": number_of_installments,
    }
    response = requests.post(
        f"{BASE_URL}/private-consignment/simulation",
        headers=_headers(),
        json=payload,
    )
    if not response.ok:
        print(f"[simulacao] ERRO {response.status_code}: {response.text}", flush=True)
    response.raise_for_status()
    data = response.json()

    return {
        "simulation_id": data["id_simulation"],
        "installment_value": data["installment_value"],
        "number_of_installments": data["number_of_installments"],
        "operation_amount": data["operation_amount"],
        "disbursement_amount": data["disbursement_amount"],
        "iof_amount": data["iof_amount"],
        "monthly_interest_rate": data["monthly_interest_rate"],
        "first_installment_date": data["first_installment_date"],
        "provider": data["provider"],
        "simulation_config_slug": data["simulation_config_slug"],
    }
