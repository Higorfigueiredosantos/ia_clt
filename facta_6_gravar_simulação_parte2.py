import requests

# Valores vindos de nós anteriores
token = "SEU_TOKEN_V8_AQUI"
cpf = "37178869836"                  # $node["BUSCA TERMO"].json["dados_trabalhador"]["dados"]["0"]["cpf"]
matricula = "MATRICULA_AQUI"         # $node["BUSCA TERMO"].json["dados_trabalhador"]["dados"]["0"]["matricula"]
codigo_tabela = "CODIGO_TABELA_AQUI" # $node["Simulação"].json["tabelas"]["0"]["codigoTabela"]
valor_operacao = "VALOR_LIQUIDO_AQUI"# $node["Simulação"].json["tabelas"]["0"]["valor_liquido"]
valor_parcela = "VALOR_PARCELA_AQUI" # $node["Simulação"].json["tabelas"]["0"]["parcela"]
coeficiente = "COEFICIENTE_AQUI"     # $node["Simulação"].json["tabelas"]["0"]["coeficiente"]

url = "https://webservice.facta.com.br/proposta/etapa1-simulador"

headers = {
    "Authorization": f"Bearer {token}",
    "Cookie": "PHPSESSID=v3j743ja0r6p6i18sq7o7da9o4"
}

data = {
    "produto": "D",
    "tipo_operacao": "13",
    "averbador": "10010",
    "convenio": "3",
    "cpf": cpf,
    "data_nascimento": "04/03/1980",
    "login_certificado": "92480_andre",
    "prazo": "24",
    "codigo_tabela": codigo_tabela,
    "valor_operacao": valor_operacao,
    "valor_parcela": valor_parcela,
    "coeficiente": coeficiente,
    "matricula": matricula
}

response = requests.post(url, headers=headers, data=data)

print(response.status_code)
print(response.json())