import requests


def verificar_autorizacao(token: str, cpf: str) -> dict:
    """Verifica se o cliente já autorizou a consulta CLT.
    Retorna dict com 'autorizado': bool e 'matricula' se autorizado."""
    r = requests.get(
        "https://webservice.facta.com.br/consignado-trabalhador/autoriza-consulta",
        headers={"Authorization": f"Bearer {token}"},
        params={"cpf": cpf},
        timeout=15,
    )
    print(f"[facta] verificar_autorizacao status={r.status_code} len={len(r.text)}", flush=True)
    if not r.ok:
        raise RuntimeError(f"Facta autoriza-consulta retornou {r.status_code}")
    data = r.json()
    erro = data.get("erro", True)
    dados = data.get("dados_trabalhador", {}).get("dados", [])
    matriculas = [d.get("matricula") for d in dados if d.get("matricula")]
    matricula = matriculas[0] if matriculas else None
    return {
        "autorizado": not erro and bool(dados),
        "matricula": matricula,
        "matriculas": matriculas,  # lista completa para tentar em caso de múltiplos empregos
        "raw": data,
    }


def enviar_termo(token: str, cpf: str, nome: str, celular: str) -> dict:
    """Envia termo de autorização via WhatsApp para o cliente.
    celular deve estar no formato '(35) 99872-2790'."""
    r = requests.post(
        "https://webservice.facta.com.br/solicita-autorizacao-consulta",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "cpf": cpf,
            "nome": nome,
            "averbador": "10010",
            "celular": celular,
            "tipo_envio": "WHATSAPP",
        },
        timeout=15,
    )
    print(f"[facta] enviar_termo status={r.status_code} len={len(r.text)}", flush=True)
    if not r.ok:
        raise RuntimeError(f"Facta solicita-autorizacao retornou {r.status_code}")
    return r.json()
