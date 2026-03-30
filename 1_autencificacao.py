import os
import requests
from dotenv import load_dotenv

load_dotenv()

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

print(f"Status: {response.status_code}")
print(response.json())
