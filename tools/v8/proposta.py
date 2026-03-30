import requests
from tools.v8.auth import get_token

BASE_URL = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io"


def _headers():
    return {"Authorization": f"Bearer {get_token()}"}


def gerar_proposta(simulation_id: str, dados_tomador: dict) -> dict:
    """
    Gera a operação final (proposta).
    Retorna dict com id da operação e formalization_url.

    dados_tomador deve conter:
    - name, email, phone (dict: country_code, area_code, number)
    - birth_date, mother_name, nationality, gender, marital_status
    - individual_document_number (CPF)
    - document_identification_type, document_identification_number, document_identification_date, document_issuer
    - address (dict: postal_code, city, state, number, street, complement, neighborhood)
    - bank (dict: transfer_method, pix_key, pix_key_type)
    - work_data (dict: employer_name, employer_document_number, registration_number)
    - political_exposition (bool)
    - person_type (default: "natural")
    """
    payload = {
        "borrower": {
            "name": dados_tomador["name"],
            "email": dados_tomador["email"],
            "phone": dados_tomador["phone"],
            "political_exposition": dados_tomador.get("political_exposition", False),
            "address": dados_tomador["address"],
            "birth_date": dados_tomador["birth_date"],
            "mother_name": dados_tomador["mother_name"],
            "nationality": dados_tomador.get("nationality", "Brasileiro"),
            "gender": dados_tomador["gender"],
            "person_type": dados_tomador.get("person_type", "natural"),
            "marital_status": dados_tomador.get("marital_status", "single"),
            "individual_document_number": dados_tomador["individual_document_number"],
            "document_identification_date": dados_tomador["document_identification_date"],
            "document_issuer": dados_tomador.get("document_issuer", "SSP"),
            "document_identification_type": dados_tomador.get("document_identification_type", "rg"),
            "document_identification_number": dados_tomador["document_identification_number"],
            "bank": dados_tomador["bank"],
            "work_data": dados_tomador["work_data"],
        },
        "simulation_id": simulation_id,
    }

    response = requests.post(
        f"{BASE_URL}/private-consignment/operation",
        headers=_headers(),
        json=payload,
    )
    if not response.ok:
        print(f"[gerar_proposta] ERRO {response.status_code}: {response.text}")
        print(f"[gerar_proposta] Payload: {payload}")
        try:
            from app.main import set_proposta_error
            set_proposta_error(payload, response.text)
        except Exception:
            pass
    response.raise_for_status()
    data = response.json()

    return {
        "operation_id": data["id"],
        "formalization_url": data["formalization_url"],
    }
