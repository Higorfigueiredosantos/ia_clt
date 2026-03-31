import requests

url = "https://webservice.facta.com.br/gera-token"

headers = {
    "Authorization": "Basic OTkwMDY6bjAzeGRqeW9hZXkzOG84bTB0aWE=",
    "Cookie": "sr1dnl931afs4koj1j417bprvn"
}

response = requests.get(url, headers=headers)

print(response.status_code)
print(response.json())