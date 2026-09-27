import pytest

from contratos import Candidata, LinhaResultado, RefEvidencia, Rotulo, Verificacao
from verificacao.refutacao import Replica
from verificacao.rotulos import deduplicar, montar_hipotese, ranquear, rotular


def _linha(lift, ic95, ic99=None, n=1000, n_resto=10000, casos=300, grupo="g", suprimido=False):
    return LinhaResultado(grupo=grupo, metrica="m", n=n, casos=casos, n_resto=n_resto, casos_resto=100, taxa=casos / n,
                          taxa_resto=0.03, lift=lift, ic95=ic95, ic99=ic99 or (ic95[0] * 0.9, ic95[1] * 1.1), suprimido=suprimido)


def _cand(grupo="g", tipo="padrao_observado", enunciado="O grupo g tem mais atrito que os demais.", metrica="m"):
    return Candidata(tipo=tipo, titulo=f"t-{grupo}", enunciado=enunciado,
                     evidencia_principal=RefEvidencia(query_id="Q01-A", grupo=grupo, metrica=metrica),
                     query_ids_citados=["Q01-A"], o_que_o_dado_responde="d", o_que_so_a_research_responde="r",
                     pergunta_para_research="p")


FORTE_A = _linha(11.7, (10.1, 13.6))
FORTE_B = _linha(12.0, (10.3, 14.0), ic99=(9.9, 14.9), n=900)


@pytest.mark.parametrize("nome, cand, rep, verificada, rotulo, regra", [
    ("P1: replica, n e lift ok", _cand(), Replica(FORTE_A, FORTE_B), True, Rotulo.EVIDENCIA, "R7"),
    ("T4: lift ≈ 1", _cand(), Replica(_linha(1.01, (0.96, 1.07)), FORTE_B), True, Rotulo.DESCARTADA, "R3"),
    ("lift protetor", _cand(), Replica(_linha(0.4, (0.3, 0.5)), FORTE_B), True, Rotulo.DESCARTADA, "R3"),
    ("grupo suprimido", _cand(), Replica(_linha(9, (5, 15), n=40, suprimido=True), FORTE_B), True, Rotulo.DESCARTADA, "R3"),
    ("T5: n pequeno", _cand(), Replica(_linha(13, (10, 17), n=69, casos=33), FORTE_B), True, Rotulo.INDICIO, "R4"),
    ("poucos casos", _cand(), Replica(_linha(3, (1.8, 5), n=500, casos=15), FORTE_B), True, Rotulo.INDICIO, "R4"),
    ("não replica", _cand(), Replica(FORTE_A, _linha(1.1, (0.9, 1.3), ic99=(0.8, 1.5))), True, Rotulo.INDICIO, "R5"),
    ("direção oposta em B", _cand(), Replica(FORTE_A, _linha(0.7, (0.5, 0.9))), True, Rotulo.INDICIO, "R5"),
    ("sem metade B", _cand(), Replica(FORTE_A, None), True, Rotulo.INDICIO, "R5"),
    ("NPS", _cand(), Replica(FORTE_A, FORTE_B, fonte="nps"), True, Rotulo.INDICIO, "R6"),
    ("número sem fonte", _cand(), Replica(FORTE_A, FORTE_B), False, Rotulo.INDICIO, "R7"),
    ("tipo causal", _cand(tipo="explicacao_causal"), Replica(FORTE_A, FORTE_B), True, Rotulo.HIPOTESE, "R2"),
    ("porque no enunciado", _cand(enunciado="Travam porque a lista é longa."), Replica(FORTE_A, FORTE_B), True, Rotulo.HIPOTESE, "R2"),
    ("P3: comportamento", _cand(), Replica(FORTE_A, FORTE_B, familia="comportamental"), True, Rotulo.HIPOTESE, "R2"),
    ("sem grupo", _cand(grupo=None), Replica(FORTE_A, FORTE_B), True, Rotulo.HIPOTESE, "R2"),
])
def test_regras_de_rotulo(nome, cand, rep, verificada, rotulo, regra):
    assert rotular(cand, rep, verificada)[:2] == (rotulo, regra), nome


def _hip(grupo, rep, dimensao="versao_app", verificada=True):
    h = montar_hipotese(_cand(grupo=grupo), rep, Verificacao(verificada=verificada))
    h.dimensao = dimensao
    return h


def test_ranking_top2_e_destino_do_resto():
    # impacto = casos_T − n_T × taxa_resto_T: a (840) > b (185) > c (37)
    hs = [
        _hip("a", Replica(FORTE_A, FORTE_B, linha_t=_linha(11.7, (10.1, 13.6), n=2000, casos=900))),
        _hip("b", Replica(FORTE_A, FORTE_B, linha_t=_linha(5, (4, 6), n=500, casos=200))),
        _hip("c", Replica(FORTE_A, FORTE_B, linha_t=_linha(3, (2, 4), n=100, casos=40))),  # 3ª evidência
        _hip("d", Replica(FORTE_A, FORTE_B, familia="comportamental")),
        _hip("e", Replica(_linha(1.0, (0.9, 1.1)), FORTE_B)),
    ]
    grupos = ranquear(deduplicar(hs))
    assert [h.candidata.evidencia_principal.grupo for h in grupos["principais"]] == ["a", "b"]
    assert [h.id for h in grupos["principais"]] == ["H1", "H2"]
    assert [h.status for h in grupos["outras_evidencias"]] == ["evidencia_extra"]
    assert grupos["research"][0].rotulo == Rotulo.HIPOTESE
    assert grupos["descartadas"][0].rotulo == Rotulo.DESCARTADA


def test_deduplicacao_junta_mesmo_grupo_e_prefere_versao_especifica():
    hs = [
        _hip("android 8.4.0", Replica(FORTE_A, FORTE_B)),
        _hip("android 8.4.0", Replica(_linha(3.9, (3.7, 4.1)), FORTE_B)),  # mesma versão, outra métrica
        _hip("android", Replica(_linha(2.0, (1.8, 2.2)), FORTE_B), dimensao="plataforma"),
    ]
    finais = deduplicar(hs)
    assert len(finais) == 1 and finais[0].candidata.evidencia_principal.grupo == "android 8.4.0"
    assert len(finais[0].evidencias_complementares) == 2
