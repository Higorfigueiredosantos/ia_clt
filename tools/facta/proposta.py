import re
import requests


def buscar_codigo_cidade(token: str, estado: str, nome_cidade: str) -> str | None:
    """Busca o código da cidade na Facta. Retorna código ou None."""
    r = requests.get(
        "https://webservice.facta.com.br/proposta-combos/cidade",
        headers={"Authorization": f"Bearer {token}"},
        params={"estado": estado.lower(), "nome_cidade": nome_cidade.lower()},
        timeout=10,
    )
    if r.ok:
        data = r.json()
        # Formato: {"erro":false,"cidade":{"660":{"nome":"BELO HORIZONTE","estado":"MG"}}}
        if isinstance(data, dict) and not data.get("erro"):
            cidade_dict = data.get("cidade", data)
            if isinstance(cidade_dict, dict):
                keys = [k for k in cidade_dict.keys() if k not in ("erro", "mensagem")]
                if keys:
                    return str(keys[0])
            # Formato alternativo: lista
            if isinstance(cidade_dict, list) and cidade_dict:
                item = cidade_dict[0]
                return str(item.get("codigo") or item.get("id") or item.get("value", ""))
        if isinstance(data, list) and data:
            return str(data[0].get("codigo") or data[0].get("id") or data[0].get("value", ""))
    return None


def detectar_tipo_pix(pix_key: str) -> str:
    """Detecta tipo da chave PIX: 1=CPF, 2=Telefone, 3=Email, 4=Aleatória."""
    digits = re.sub(r'\D', '', pix_key)
    if len(digits) == 11:
        return "1"
    if '@' in pix_key:
        return "3"
    if len(digits) >= 10:
        return "2"
    return "4"


def gravar_dados_pessoais(
    token: str,
    id_simulador: str,
    cpf: str,
    nome: str,
    sexo: str,
    data_nascimento: str,
    rg: str,
    estado_rg: str,
    nome_mae: str,
    celular: str,
    renda: float,
    cep: str,
    endereco: str,
    numero: str,
    bairro: str,
    estado: str,
    codigo_cidade: str,
    matricula: str,
    pix_key: str,
    tipo_chave_pix: str,
) -> dict:
    """Grava dados pessoais (etapa 2). Retorna dict com codigo_cliente."""
    r = requests.post(
        "https://webservice.facta.com.br/proposta/etapa2-dados-pessoais",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "id_simulador": id_simulador,
            "cpf": cpf,
            "nome": nome,
            "sexo": sexo,
            "estado_civil": "1",
            "data_nascimento": data_nascimento,
            "rg": rg,
            "estado_rg": estado_rg,
            "orgao_emissor": "SSP",
            "data_expedicao": "13/01/2021",
            "estado_natural": estado_rg,
            "cidade_natural": codigo_cidade,
            "nacionalidade": "1",
            "celular": celular,
            "renda": str(renda),
            "cep": cep,
            "endereco": endereco,
            "numero": numero,
            "bairro": bairro,
            "estado": estado_rg,
            "cidade": codigo_cidade,
            "nome_mae": nome_mae,
            "nome_pai": "NAO DECLARADO",
            "matricula": matricula,
            "cliente_iletrado_impossibilitado": "N",
            "valor_patrimonio": "1",
            "tipo_chave_pix": tipo_chave_pix,
            "chave_pix": pix_key,
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def digitar_proposta(token: str, codigo_cliente: str, id_simulador: str) -> dict:
    """Grava etapa 3 (cadastro da proposta). Retorna link para assinatura."""
    r = requests.post(
        "https://webservice.facta.com.br/proposta/etapa3-proposta-cadastro",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "codigo_cliente": (None, str(codigo_cliente)),
            "id_simulador": (None, str(id_simulador)),
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()
