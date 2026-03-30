import requests
from tools.v8.auth import get_token

BASE_URL = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io"


def _headers():
    return {"Authorization": f"Bearer {get_token()}"}


def buscar_dados_cliente(cpf: str) -> dict:
    """
    Busca dados básicos do cliente pelo CPF.
    Retorna dict com name, birthDate, email, phoneRegionCode, phoneNumber, gender.
    """
    cpf_limpo = cpf.replace(".", "").replace("-", "").zfill(11)
    response = requests.get(
        f"{BASE_URL}/private-consignment/consult/client-data/basic/{cpf_limpo}",
        headers=_headers(),
    )
    response.raise_for_status()
    return response.json()
