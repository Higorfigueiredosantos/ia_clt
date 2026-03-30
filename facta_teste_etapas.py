"""
Testa etapa1 (gravar simulacao) e etapa2 (dados pessoais) para ver os campos de retorno.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

CPF = "06878913969"  # CPF do teste real

from tools.facta.auth import get_facta_token
from tools.facta.consulta import verificar_autorizacao
from tools.facta.simulacao import buscar_simulacoes, gravar_simulacao

print("=" * 60)

# Token
token = get_facta_token()
print(f"[1] token OK: {token[:30]}...")

# Auth
auth = verificar_autorizacao(token, CPF)
matricula = auth["matricula"]
dados_d = auth["raw"]["dados_trabalhador"]["dados"][0]
data_nasc = dados_d["dataNascimento"]
print(f"[2] autorizado={auth['autorizado']} matricula={matricula} nasc={data_nasc}")

# Simulacao
import re
renda_str = re.sub(r"[^\d,]", "", dados_d.get("valorBaseMargem", "0")).replace(",", ".")
renda = float(renda_str) if renda_str else 0.0
sims = buscar_simulacoes(token, CPF, data_nasc, renda, 24)
melhor = sims[0]
print(f"[3] simulacao selecionada: tabela={melhor['tabela']} codigoTabela={melhor['codigoTabela']}")
print(f"    contrato={melhor['contrato']} parcela={melhor['parcela']} coef={melhor['coeficiente']} prazo={melhor['prazo']}")

# Etapa 1 - gravar simulacao
print("\n[4] Gravando simulacao (etapa1)...")
resultado_sim = gravar_simulacao(
    token, CPF, data_nasc,
    str(melhor["codigoTabela"]),
    float(melhor["contrato"]),
    float(melhor["coeficiente"]),
    int(melhor["prazo"]),
    float(melhor["parcela"]),
    matricula,
)
print(f"    Resposta completa: {resultado_sim}")
print(f"    Chaves: {list(resultado_sim.keys()) if isinstance(resultado_sim, dict) else type(resultado_sim)}")

print("\n" + "=" * 60)
