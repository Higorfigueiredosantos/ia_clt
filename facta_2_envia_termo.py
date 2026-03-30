import requests

# Token obtido no passo anterior (HTTP Request2)
token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIzOTAiLCJsdmwiOiIyIiwidXNyIjoiOTI0ODAiLCJjcnQiOiI5MjQ4MCIsImlhdCI6MTc3NDcyMDExNSwiZXhwIjoxNzc0NzIzNzE1fQ.IBCos-ITTxR7L8wyEfFbcOZWgDWh6WY_s_wUZwOO5zA"

url = "https://webservice.facta.com.br/solicita-autorizacao-consulta"

headers = {
    "Authorization": f"Bearer {token}",
    "Cookie": "PHPSESSID=l10ofrjr6ol957d6b3q7bddqp2"
}

data = {
    "cpf": "37178869836",
    "nome": "LUIZA EVANGELINA SILVA DOS SANTOS",
    "averbador": "10010",
    "celular": "(35) 99872-2790",
    "tipo_envio": "WHATSAPP"
}

response = requests.post(url, headers=headers, data=data)

print(response.status_code)
print(response.json())