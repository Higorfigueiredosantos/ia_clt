import requests

# Valores vindos de nós anteriores
token = "SEU_TOKEN_V8_AQUI"
codigo_cliente = "CODIGO_CLIENTE_AQUI"  # $node["HTTP Request1"].json["codigo_cliente"]
id_simulador = "ID_SIMULADOR_AQUI"      # $node["Simulação de valores da operação"].json["id_simulador"]

url = "https://webservice.facta.com.br/proposta/etapa3-proposta-cadastro"

headers = {
    "Authorization": f"Bearer {token}"
}

files = {
    "codigo_cliente": (None, codigo_cliente),
    "id_simulador": (None, id_simulador)
}

response = requests.post(url, headers=headers, files=files)

print(response.status_code)
print(response.json())