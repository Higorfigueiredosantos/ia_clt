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


def buscar_consulta(consult_id):
    token = get_token()

    url = f"https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io/private-consignment/consult/{consult_id}"
    headers = {"Authorization": f"Bearer {token}"}

    response = requests.get(url, headers=headers)
    print(f"Status: {response.status_code}")
    print(response.json())


if __name__ == "__main__":
    buscar_consulta(consult_id="d75995a4-cfe7-4212-825e-acfbd1aea611")
