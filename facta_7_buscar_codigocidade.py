import requests

# Token vindo do nó anterior
token = "SEU_TOKEN_V8_AQUI"

url = "https://webservice.facta.com.br/proposta-combos/cidade"

headers = {
    "Authorization": f"Bearer {token}"
}

params = {
    "estado": "mg",
    "nome_cidade": "belo horizonte"
}

response = requests.get(url, headers=headers, params=params)

print(response.status_code)
print(response.json())