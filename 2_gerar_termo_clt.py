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


def consultar(cpf, nome, email, celular, data_nasc, genero):
    token = get_token()

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

    url = "https://v8-bff-prod.yellowisland-b252a8a0.eastus.azurecontainerapps.io/private-consignment/consult"
    headers = {"Authorization": f"Bearer {token}"}

    response = requests.post(url, headers=headers, json=payload)
    print(f"Status: {response.status_code}")
    print(response.json())


if __name__ == "__main__":
    consultar(
        cpf="02494823633",
        nome="NIVALDO ALVES LOPES",
        email="fatliimaalvorada@hotmail.com",
        celular="98982086370",
        data_nasc="1972-05-05",
        genero="male",
    )
