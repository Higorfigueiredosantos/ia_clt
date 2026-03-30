import requests

# Valores vindos de nós anteriores
token = "SEU_TOKEN_V8_AQUI"
id_simulador = "ID_SIMULADOR_AQUI"  # $node["Simulação de valores da operação"].json["id_simulador"]
matricula = "MATRICULA_AQUI"        # $node["BUSCA TERMO"].json["dados_trabalhador"]["dados"]["0"]["matricula"]

url = "https://webservice.facta.com.br/proposta/etapa2-dados-pessoais"

headers = {
    "Authorization": f"Bearer {token}",
    "Cookie": "PHPSESSID=v3j743ja0r6p6i18sq7o7da9o4"
}

data = {
    "id_simulador": id_simulador,
    "cpf": "65892461300",
    "nome": "REMIVAL PEREIRA DOS SANTOS",
    "sexo": "M",
    "estado_civil": "1",
    "data_nascimento": "04/03/1980",
    "rg": "18169188",
    "estado_rg": "MG",
    "orgao_emissor": "SSP",
    "data_expedicao": "13/02/2020",
    "estado_natural": "MG",
    "cidade_natural": "660",
    "nacionalidade": "1",
    "celular": "(034) 99856-1527",
    "renda": "2121.59",
    "cep": "30640440",
    "endereco": "R CB VALERIO SANTOS",
    "numero": "92",
    "bairro": "ATILA DE PAIVA BARREIRO",
    "estado": "MG",
    "cidade": "660",
    "nome_mae": "MARIA APARECIDA NASCIMENTO DE JESUS",
    "nome_pai": "NAO DECLARADO",
    "matricula": matricula,
    "cliente_iletrado_impossibilitado": "N",
    "valor_patrimonio": "1",
    "tipo_chave_pix": "1",
    "chave_pix": "65892461300"
}

response = requests.post(url, headers=headers, data=data)

print(response.status_code)
print(response.json())