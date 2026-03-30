import os
import requests
from dotenv import load_dotenv

load_dotenv()


def get_token():
    url = os.getenv("V8_AUTH_URL")
    payload = {
        "grant_type": "password",
        "audience": "https://bff.v8sistema.com",
        "scope": "offline_access",
        "client_id": os.getenv("V8_CLIENT_ID"),
        "username": os.getenv("V8_USERNAME"),
        "password": os.getenv("V8_PASSWORD"),
    }
    response = requests.post(url, data=payload)
    response.raise_for_status()
    return response.json()["access_token"]


def gerar_proposta(simulation_id):
    token = get_token()

    url = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io/private-consignment/operation"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "borrower": {
            "name": "NIVALDO ALVES LOPES",
            "email": "fatliimaalvorada@hotmail.com",
            "phone": {
                "country_code": "55",
                "area_code": "98",
                "number": "982086370",
            },
            "political_exposition": False,
            "address": {
                "postal_code": "38020310",
                "city": "Itau de minas",
                "state": "MG",
                "number": "52",
                "street": "Rua 1",
                "complement": "",
                "neighborhood": "São Benedito",
            },
            "birth_date": "1972-05-05",
            "mother_name": "GENILDA ALVES LOPES",
            "nationality": "Brasileiro",
            "gender": "male",
            "person_type": "natural",
            "marital_status": "single",
            "individual_document_number": "02494823633",
            "document_identification_date": "2021-05-22",
            "document_issuer": "SSP",
            "document_identification_type": "rg",
            "document_identification_number": "61060025337",
            "bank": {
                "transfer_method": "pix",
                "pix_key": "94eccedd-460f-482c-9287-1700552c743f",
                "pix_key_type": "random",
            },
            "work_data": {
                "employer_name": "DENVER SOLDAS LTDA",
                "employer_document_number": "22671564",
                "registration_number": "2267156400019900920",
            },
        },
        "simulation_id": simulation_id,
    }

    response = requests.post(url, headers=headers, json=payload)
    print(f"Status: {response.status_code}")
    print(response.json())


if __name__ == "__main__":
    gerar_proposta(simulation_id="c540c0c2-6986-43a6-a0a4-cd5808654dda")
