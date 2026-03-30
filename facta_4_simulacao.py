import requests

# Valores vindos de nós anteriores no n8n
token = "SEU_TOKEN_V8_AQUI"
cpf = "37178869836"  # $node["BUSCA TERMO"].json["dados_trabalhador"]["dados"]["0"]["cpf"]

url = "https://webservice.facta.com.br/proposta/operacoes-disponiveis"

headers = {
    "Authorization": f"Bearer {token}",
    "Cookie": "PHPSESSID=v3j743jaor6p6i18sq7o7da9o4"
}

params = {
    "produto": "D",
    "tipo_operacao": "13",
    "averbador": "10010",
    "convenio": "3",
    "opcao_valor": "2",
    "cpf": cpf,
    "data_nascimento": "04/03/1980",
    "prazo": "24",
    "valor_renda": "2121.59",
    "valor_parcela": "250"
}

response = requests.get(url, headers=headers, params=params)

print(response.status_code)
print(response.json())