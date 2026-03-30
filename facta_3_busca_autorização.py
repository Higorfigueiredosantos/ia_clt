import requests

# Token obtido no passo anterior (HTTP Request2)
token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIzOTAiLCJsdmwiOiIyIiwidXNyIjoiOTI0ODAiLCJjcnQiOiI5MjQ4MCIsImlhdCI6MTc3NDcwMjI2NSwiZXhwIjoxNzc0NzA1ODY1fQ.9ulkckoUStlBNn63rXU4jMTuW_wcax-L9VQ-ngA5NkA"

url = "https://webservice.facta.com.br/consignado-trabalhador/autoriza-consulta"

headers = {
    "Authorization": f"Bearer {token}",
    "Cookie": "PHPSESSID=l10ofrjr6ol957d6b3q7bddqp2"
}

params = {
    "cpf": "37178869836"
}

response = requests.get(url, headers=headers, params=params)

print(response.status_code)
print(response.json())