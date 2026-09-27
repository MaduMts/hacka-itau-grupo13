"""O script de avaliação roda sem rede com o agente roteirizado e confere o gabarito."""
import importlib.util
from pathlib import Path

from agente.cliente_devin import ClienteRoteirizado
from agente.roteiros import roteiro_gabarito

_spec = importlib.util.spec_from_file_location("avaliar_agente", Path(__file__).resolve().parents[1] / "scripts" / "avaliar_agente.py")
avaliar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(avaliar)


def test_uma_execucao_roteirizada_acerta_o_gabarito(pasta_normal):
    relato = avaliar.uma_execucao(ClienteRoteirizado(roteiro_gabarito), "normal", avaliar.PALPITE_DEMO, 1)
    assert relato["erro"] is None
    assert all(item["ok"] for item in relato["gabarito"].values())
    assert {h["rotulo"] for h in relato["hipoteses"]} >= {"Evidência", "Hipótese"}
