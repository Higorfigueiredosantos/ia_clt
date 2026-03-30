import re
import requests


_SESSION_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://app.multicorban.com",
    "Referer": "https://app.multicorban.com/search/form/geral",
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5) AppleWebKit/537.36",
}


def _first(html, patterns):
    for p in patterns:
        m = re.search(p, html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""

def _clean(s):
    return re.sub(r'\s+', ' ', s.replace('&nbsp;', ' ')).strip() if s else ""

def _money(s):
    if not s:
        return None
    v = re.sub(r'[^\d,.\-]', '', s).replace('.', '').replace(',', '.')
    try:
        return float(v)
    except ValueError:
        return None

def _digits(s):
    return re.sub(r'\D', '', str(s or ''))


def buscar_dados_cliente_facta(cpf: str) -> dict:
    """Busca dados do cliente no multicorban. Retorna dict com nome, data_nascimento, renda, sexo, nome_mae."""
    session = requests.Session()
    session.post(
        "https://app.multicorban.com/access/validateLogin",
        headers=_SESSION_HEADERS,
        data={"login": "andreluiz", "senha": "Andre21*"},
        verify=False,
    )

    r = session.post(
        "https://app.multicorban.com/search/consult",
        headers=_SESSION_HEADERS,
        data={
            "methodOperation": "dataBaseFgts",
            "methodConsult": "cpf",
            "dataConsult": _digits(cpf),
            "dataOrgao": "", "CPF": "", "CPFRepresentante": "", "ddd": "", "telefone": "",
        },
        verify=False,
    )

    try:
        payload = r.json()
    except Exception:
        payload = {"hash": r.text}

    html = str(payload.get("hash", ""))

    nome = _clean(_first(html, [
        r'id=["\']nome_beneficiario["\'][^>]*>\s*([^<]+)\s*<\/small>',
        r'<p[^>]*>\s*Nome\s*<\/p>\s*<small[^>]*>\s*([^<]+)\s*<\/small>',
    ]))

    data_nasc = _clean(_first(html, [
        r'<p[^>]*>\s*Data de Nascimento\s*<\/p>[\s\S]*?<small[^>]*>\s*([\d]{2}\/[\d]{2}\/[\d]{4})\s*<\/small>',
    ]))

    sexo_raw = _clean(_first(html, [
        r'<p[^>]*>\s*SEXO\s*<\/p>[\s\S]*?<small[^>]*>\s*([^<]+)\s*<\/small>',
    ]))
    sexo = "M" if "masc" in sexo_raw.lower() else "F"

    nome_mae = _clean(_first(html, [
        r'<p[^>]*>\s*Nome da Mãe\s*<\/p>[\s\S]*?<small[^>]*>\s*([^<]*)\s*<\/small>',
    ]))

    renda_str = _clean(_first(html, [
        r'id=["\']renda["\'][^>]*>[\s\S]*?<small[^>]*text-success[^>]*>\s*(R\$\s*[\d\.,]+)\s*<\/small>',
        r'<p[^>]*>\s*Renda\s*<\/p>[\s\S]*?<small[^>]*text-success[^>]*>\s*(R\$\s*[\d\.,]+)\s*<\/small>',
    ]))

    return {
        "nome": nome or None,
        "data_nascimento": data_nasc or None,
        "sexo": sexo,
        "nome_mae": nome_mae or "NAO DECLARADO",
        "renda": _money(renda_str) or 0.0,
        "has_data": bool(nome),
    }
