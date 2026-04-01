import requests

# Ordem de preferência das tabelas
_TABELA_ORDEM = ["64157", "64173", "64106", "64165"]


def _ordenar(simulacoes: list) -> list:
    def key(s):
        tabela = s.get("tabela", "")
        for i, cod in enumerate(_TABELA_ORDEM):
            if tabela.startswith(cod):
                return i
        return len(_TABELA_ORDEM)
    return sorted(simulacoes, key=key)


class MatriculaRequeridaError(Exception):
    """Facta exige matrícula para simular. matriculas contém as opções disponíveis."""
    def __init__(self, matriculas: list[str]):
        self.matriculas = matriculas
        super().__init__(f"Matrícula requerida: {matriculas}")


def buscar_simulacoes(
    token: str,
    cpf: str,
    data_nascimento: str,
    valor_renda: float,
    prazo: int = 24,
    valor_parcela: float = None,
    matricula: str = None,
) -> list:
    """Busca simulações disponíveis. Retorna lista ordenada por preferência."""
    import re as _re
    # Se não informado, usa 35% da renda como parcela
    if not valor_parcela:
        valor_parcela = round(valor_renda * 0.35, 2)

    params = {
        "produto": "D",
        "tipo_operacao": "13",
        "averbador": "10010",
        "convenio": "3",
        "opcao_valor": "2",
        "cpf": cpf,
        "data_nascimento": data_nascimento,
        "prazo": str(prazo),
        "valor_renda": str(valor_renda),
        "valor_parcela": str(valor_parcela),
    }
    if matricula:
        params["matricula"] = str(matricula)

    r = requests.get(
        "https://webservice.facta.com.br/proposta/operacoes-disponiveis",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    # API retorna {"erro": false, "tabelas": [...]} ou lista direta
    if isinstance(data, dict):
        if data.get("erro"):
            mensagem = data.get("mensagem", "")
            # Detecta exigência de matrícula e extrai as opções
            if "matricula" in mensagem.lower():
                matriculas = _re.findall(r'\b\d{4,}\b', mensagem)
                if matriculas:
                    raise MatriculaRequeridaError(matriculas)
            raise ValueError(f"Erro na simulacao Facta: {data}")
        simulacoes = data.get("tabelas") or []
    elif isinstance(data, list):
        simulacoes = data
    else:
        raise ValueError(f"Resposta inesperada da simulacao Facta: {data}")
    return _ordenar(simulacoes)


def gravar_simulacao(
    token: str,
    cpf: str,
    data_nascimento: str,
    codigo_tabela: str,
    valor_operacao: float,
    coeficiente: float,
    prazo: int,
    valor_parcela: float,
    matricula: str,
) -> dict:
    """Grava a simulação (etapa 1) e retorna o id_simulador."""
    data = {
        "produto": "D",
        "tipo_operacao": "13",
        "averbador": "10010",
        "convenio": "3",
        "cpf": cpf,
        "data_nascimento": data_nascimento,
        "login_certificado": "99006_leila",
        "prazo": str(prazo),
        "codigo_tabela": str(codigo_tabela),
        "valor_operacao": str(valor_operacao),
        "valor_parcela": str(valor_parcela),
        "coeficiente": str(coeficiente),
        "matricula": str(matricula),
    }
    r = requests.post(
        "https://webservice.facta.com.br/proposta/etapa1-simulador",
        headers={"Authorization": f"Bearer {token}"},
        data=data,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()
