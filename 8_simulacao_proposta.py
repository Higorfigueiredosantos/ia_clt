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


def simular_proposta(consult_id, config_id, installment_face_value, number_of_installments):
    token = get_token()

    url = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io/private-consignment/simulation"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "config_id": config_id,
        "consult_id": consult_id,
        "installment_face_value": installment_face_value,
        "number_of_installments": number_of_installments,
    }

    response = requests.post(url, headers=headers, json=payload)
    print(f"Status: {response.status_code}")
    print(response.json())


if __name__ == "__main__":
    simular_proposta(
        consult_id="d75995a4-cfe7-4212-825e-acfbd1aea611",
        config_id="fbbb3a06-05ca-4567-9a92-ce78cb4db796",
        installment_face_value=325.45,
        number_of_installments=24,
    )
