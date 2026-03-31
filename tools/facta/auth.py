import requests


def get_facta_token() -> str:
    """Gera um novo token Facta."""
    r = requests.get(
        "https://webservice.facta.com.br/gera-token",
        headers={
            "Authorization": "Basic OTkwMDY6bjAzeGRqeW9hZXkzOG84bTB0aWE=",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]
