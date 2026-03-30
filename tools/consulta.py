# Redirecionamento para tools.v8.consulta (compatibilidade)
from tools.v8.consulta import (
    ConsultaEmAnaliseError,
    ConsultaRejeitadaError,
    buscar_consulta_ativa,
    gerar_consulta,
    autorizar_consulta,
    buscar_dados_consulta,
    executar_fluxo_consulta,
)

__all__ = [
    "ConsultaEmAnaliseError",
    "ConsultaRejeitadaError",
    "buscar_consulta_ativa",
    "gerar_consulta",
    "autorizar_consulta",
    "buscar_dados_consulta",
    "executar_fluxo_consulta",
]
