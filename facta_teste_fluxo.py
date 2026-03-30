"""
Teste completo do fluxo Facta para um CPF específico.
Roda: token → dados cliente → autorização → simulação
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

CPF = "65892461300"

from tools.facta.auth import get_facta_token
from tools.facta.dados_cliente import buscar_dados_cliente_facta
from tools.facta.consulta import verificar_autorizacao, enviar_termo
from tools.facta.simulacao import buscar_simulacoes

print("=" * 60)
print(f"TESTE FACTA — CPF: {CPF}")
print("=" * 60)

# 1. Token
print("\n[1] Gerando token...")
try:
    token = get_facta_token()
    print(f"    OK — token: {token[:30]}...")
except Exception as e:
    print(f"    ERRO: {e}")
    sys.exit(1)

# 2. Dados do cliente (Multicorban)
print("\n[2] Buscando dados do cliente (Multicorban)...")
try:
    cliente = buscar_dados_cliente_facta(CPF)
    print(f"    Nome:            {cliente.get('nome')}")
    print(f"    Data nascimento: {cliente.get('data_nascimento')}")
    print(f"    Sexo:            {cliente.get('sexo')}")
    print(f"    Renda:           R$ {cliente.get('renda')}")
    print(f"    Nome mae:        {cliente.get('nome_mae')}")
    print(f"    has_data:        {cliente.get('has_data')}")
except Exception as e:
    print(f"    ERRO: {e}")
    cliente = {}

# 3. Autorização
print("\n[3] Verificando autorização CLT...")
try:
    auth = verificar_autorizacao(token, CPF)
    print(f"    Autorizado: {auth['autorizado']}")
    print(f"    Matricula:  {auth.get('matricula')}")
    print(f"    Raw:        {auth.get('raw')}")
except Exception as e:
    print(f"    ERRO: {e}")
    auth = {"autorizado": False}

# 4. Simulação (só se tiver dados)
data_nasc = cliente.get("data_nascimento", "")
renda = float(cliente.get("renda") or 0)

if not data_nasc or renda == 0:
    print("\n[4] Pulando simulação — sem data_nascimento ou renda.")
else:
    print(f"\n[4] Buscando simulacoes (renda={renda}, prazo=24)...")
    try:
        simulacoes = buscar_simulacoes(token, CPF, data_nasc, renda, prazo=24)
        print(f"    Total encontrado: {len(simulacoes)}")
        for i, s in enumerate(simulacoes[:3]):
            print(f"\n    Opção {i+1}:")
            for k, v in s.items():
                print(f"      {k}: {v}")
    except Exception as e:
        print(f"    ERRO: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "=" * 60)
print("FIM DO TESTE")
print("=" * 60)
