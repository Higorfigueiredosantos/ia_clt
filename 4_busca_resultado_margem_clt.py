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


def buscar_margem(cpf, start_date, end_date, page=1, limit=10):
    token = get_token()

    url = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io/private-consignment/consult"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "search": cpf,
        "page": page,
        "limit": limit,
    }

    response = requests.get(url, headers=headers, params=params)
    print(f"Status: {response.status_code}")
    print(response.json())


if __name__ == "__main__":
    buscar_margem(
        cpf="02494823633",
        start_date="2026-03-09T03:00:00.000Z",
        end_date="2026-03-26T02:59:59.999Z",
    )
