import requests

url = "https://webservice.facta.com.br/gera-token"

headers = {
    "Authorization": "Basic OTI0ODA6YzZmeHIydmR3NWc3d2dvOWNpeTY=",
    "Cookie": "9690genf5s8v50p6r9k7der5vd"
}

response = requests.get(url, headers=headers)

print(response.status_code)
print(response.json())