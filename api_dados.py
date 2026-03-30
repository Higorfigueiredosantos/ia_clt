import requests
import re

session = requests.Session()

base_headers = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://app.multicorban.com",
    "Referer": "https://app.multicorban.com/search/form/geral",
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36 Edg/143.0.0.0"
}

# ── 1. LOGIN ─────────────────────────────────────────────────────────
session.post(
    "https://app.multicorban.com/access/validateLogin",
    headers=base_headers,
    data={"login": "andreluiz", "senha": "Andre21*"},
    verify=False
)

# ── 2. REQUISIÇÃO FGTS ──────────────────────────────────────────────
response = session.post(
    "https://app.multicorban.com/search/consult",
    headers=base_headers,
    data={
        "methodOperation": "dataBaseFgts",
        "methodConsult": "cpf",
        "dataConsult": "65892461300",
        "dataOrgao": "",
        "CPF": "",
        "CPFRepresentante": "",
        "ddd": "",
        "telefone": ""
    },
    verify=False
)

raw_text = response.text

try:
    payload = response.json()
except Exception:
    payload = {"hash": raw_text}

html = str(payload.get("hash", ""))

# ── 3. FUNÇÕES AUXILIARES ────────────────────────────────────────────

def first_match(html, patterns, group=1):
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m and m.group(group) is not None:
            return m.group(group).strip()
    return ""

def clean_text(s):
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s.replace('&nbsp;', ' ')).strip()

def to_int_or_none(s):
    digits = re.sub(r'\D', '', str(s))
    return int(digits) if digits else None

def br_money_to_number(s):
    if not s:
        return None
    v = re.sub(r'[^\d,.\-]', '', str(s)).replace('.', '').replace(',', '.')
    try:
        return float(v)
    except ValueError:
        return None

def only_digits(s):
    return re.sub(r'\D', '', str(s or ''))

# ── 4. EXTRAÇÃO DO HTML ──────────────────────────────────────────────

empresa_razao = clean_text(first_match(html, [
    r'<p[^>]*>\s*Razao Social\s*<\/p>\s*<small[^>]*>\s*([^<]+)\s*<\/small>',
    r'<p[^>]*>\s*Razão Social\s*<\/p>\s*<small[^>]*>\s*([^<]+)\s*<\/small>',
]))

empresa_cnpj = only_digits(first_match(html, [
    r'<p[^>]*>\s*CNPJ\s*<\/p>[\s\S]*?<small[^>]*>\s*([\d.\-\/ ]+)\s*<\/small>',
]))

total_registros = to_int_or_none(first_match(html, [
    r'<p[^>]*>\s*Total de Registros\s*<\/p>[\s\S]*?<small[^>]*id=["\']contador["\'][^>]*>\s*([\d]+)\s*<\/small>',
    r'<p[^>]*>\s*Total de Registros\s*<\/p>[\s\S]*?<small[^>]*>\s*([\d]+)\s*<\/small>',
]))

nome = clean_text(first_match(html, [
    r'id=["\']nome_beneficiario["\'][^>]*>\s*([^<]+)\s*<\/small>',
    r'<p[^>]*>\s*Nome\s*<\/p>\s*<small[^>]*>\s*([^<]+)\s*<\/small>',
]))

cpf = only_digits(first_match(html, [
    r'id=["\']cpf_beneficiario["\'][^>]*>[\s\S]*?([\d]{11})',
    r'<p[^>]*>\s*CPF\s*<\/p>[\s\S]*?<small[^>]*>\s*([\d]{11})\s*<\/small>',
]))

data_nascimento = clean_text(first_match(html, [
    r'<p[^>]*>\s*Data de Nascimento\s*<\/p>[\s\S]*?<small[^>]*>\s*([\d]{2}\/[\d]{2}\/[\d]{4})\s*<\/small>',
]))

idade = to_int_or_none(first_match(html, [
    r'<p[^>]*>\s*Idade\s*<\/p>[\s\S]*?<small[^>]*>\s*([\d]{1,3})\s*Anos',
]))

sexo = clean_text(first_match(html, [
    r'<p[^>]*>\s*SEXO\s*<\/p>[\s\S]*?<small[^>]*>\s*([^<]+)\s*<\/small>',
]))

nome_mae = clean_text(first_match(html, [
    r'<p[^>]*>\s*Nome da Mãe\s*<\/p>[\s\S]*?<small[^>]*>\s*([^<]*)\s*<\/small>',
]))

data_admissao = clean_text(first_match(html, [
    r'<p[^>]*>\s*Data da Admissão\s*<\/p>[\s\S]*?<small[^>]*>\s*([\d]{2}\/[\d]{2}\/[\d]{4})\s*<\/small>',
]))

tempo_contrib_meses = to_int_or_none(first_match(html, [
    r'Tempo de Contribuição\s*\(Meses\)[\s\S]*?<small[^>]*text-success[^>]*>\s*([\d]+)\s*<\/small>',
]))

situacao = clean_text(first_match(html, [
    r'<p[^>]*>\s*Situação\s*<\/p>[\s\S]*?<small[^>]*text-info[^>]*>\s*([^<]+)\s*<\/small>',
]))

renda_str = clean_text(first_match(html, [
    r'id=["\']renda["\'][^>]*>[\s\S]*?<small[^>]*text-success[^>]*>\s*(R\$\s*[\d\.,]+)\s*<\/small>',
    r'<p[^>]*>\s*Renda\s*<\/p>[\s\S]*?<small[^>]*text-success[^>]*>\s*(R\$\s*[\d\.,]+)\s*<\/small>',
]))

saldo_aprox_str = clean_text(first_match(html, [
    r'<p[^>]*>\s*Saldo Aproximado\s*<\/p>[\s\S]*?<small[^>]*text-success[^>]*>\s*(R\$\s*[\d\.,]+)\s*<\/small>',
]))

renda = br_money_to_number(renda_str)
saldo_aproximado = br_money_to_number(saldo_aprox_str)

phone_matches = re.findall(r'api\.whatsapp\.com\/send\?[^"\']*phone=55(\d{10,13})', html, re.IGNORECASE)
anchor_matches = re.findall(r'class=["\']phone_with_ddd[^"\']*["\'][\s\S]*?>\s*(\d{10,13})\s*<\/a>', html, re.IGNORECASE)
telefones = list(set([only_digits(t) for t in phone_matches + anchor_matches if t]))

# ── 5. RESULTADO FINAL ───────────────────────────────────────────────

resultado = {
    "source_code": payload.get("code"),
    "empresa": {
        "razao_social": empresa_razao or None,
        "cnpj": empresa_cnpj or None,
        "total_registros": total_registros,
    },
    "pessoa": {
        "nome": nome or None,
        "cpf": cpf or None,
        "data_nascimento": data_nascimento or None,
        "idade": idade,
        "sexo": sexo or None,
        "nome_mae": nome_mae or None,
    },
    "trabalho": {
        "data_admissao": data_admissao or None,
        "tempo_contribuicao_meses": tempo_contrib_meses,
        "situacao": situacao or None,
        "renda_texto": renda_str or None,
        "renda_valor": renda,
        "saldo_aproximado_texto": saldo_aprox_str or None,
        "saldo_aproximado_valor": saldo_aproximado,
    },
    "contatos": {
        "telefones": telefones,
    },
    "raw": {
        "has_html": bool(html and "<article" in html),
    }
}

print(resultado)
