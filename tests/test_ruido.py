"""Ruído não vira evidência: simulação de grupos sem efeito nenhum (mesma taxa que o resto).

O agente vê ~100 linhas grupo × métrica por execução; algumas passam na metade A por acaso.
A réplica na metade B (independente, IC99) é o que segura as falsas evidências.
"""
import numpy as np

from contratos import Candidata, RefEvidencia, Rotulo
from motor.estatistica import montar_linhas
from verificacao.refutacao import Replica
from verificacao.rotulos import rotular

CAND = Candidata(tipo="padrao_observado", titulo="t", enunciado="O grupo g tem mais atrito que os demais.",
                 evidencia_principal=RefEvidencia(query_id="Q01-A", grupo="g", metrica="m"), query_ids_citados=["Q01-A"],
                 o_que_o_dado_responde="d", o_que_so_a_research_responde="r", pergunta_para_research="p")


def _linha(n_g, n_r, casos_g, casos_r):
    return montar_linhas("m", [("g", n_g, int(casos_g))], n_g + n_r, int(casos_g + casos_r))[0]


def _taxa_evidencia(n_g, n_r, p, simulacoes, a_forte: bool, semente=7):
    rng = np.random.default_rng(semente)
    forte = _linha(n_g, n_r, int(n_g * 0.35), int(n_r * 0.03))  # passa na metade A com folga
    evidencias = 0
    for _ in range(simulacoes):
        la = forte if a_forte else _linha(n_g, n_r, rng.binomial(n_g, p), rng.binomial(n_r, p))
        lb = _linha(n_g, n_r, rng.binomial(n_g, p), rng.binomial(n_r, p))
        evidencias += rotular(CAND, Replica(la, lb), True)[0] == Rotulo.EVIDENCIA
    return evidencias / simulacoes


def test_grupo_nulo_que_passou_em_a_quase_nunca_replica_em_b():
    # Tamanho típico de grupo no FullStory (amostra de 10%): ~750 usuários, taxa-base 2,5%
    assert _taxa_evidencia(750, 8000, 0.025, 5000, a_forte=True) < 0.015


def test_grupo_nulo_praticamente_nunca_vira_evidencia():
    assert _taxa_evidencia(750, 8000, 0.025, 5000, a_forte=False) < 0.002
    assert _taxa_evidencia(9000, 80000, 0.06, 2000, a_forte=False) == 0.0  # tamanho típico de tagueamento
