from types import SimpleNamespace

import pytest

from contratos import Candidata, LinhaResultado, RefEvidencia, ResultadoConsulta
from verificacao.numeros import extrair_numeros, verificar_candidata


class RegistroFalso:
    def __init__(self, *resultados, valores=None):
        self.resultados = {r.query_id: r for r in resultados}
        self.base = SimpleNamespace(valores=valores or {"faixa_etaria": ["60+", "18-24"], "versao_app": ["android 8.4.0"]})

    def obter(self, qid):
        return self.resultados.get(qid)


def _res(qid, **linha):
    padrao = dict(grupo="60+", metrica="rage_click", n=1234, casos=152, n_resto=8700, casos_resto=348, taxa=0.1234,
                  taxa_resto=0.04, lift=2.46, ic95=(2.1, 2.9), ic99=(2.0, 3.0), participacao=0.317, casos_extrapolados=45210)
    padrao.update(linha)
    return ResultadoConsulta(query_id=qid, ferramenta="atrito", params={"segmentar_por": "faixa_etaria"}, metade="A",
                             fonte="fullstory", linhas=[LinhaResultado(**padrao)])


REG = RegistroFalso(
    _res("Q01-A"),
    _res("Q02-A", grupo="18-24", taxa=0.777, participacao=0.36, casos_extrapolados=3390),
    _res("Q03-A", grupo="android 8.4.0", taxa=0.05, n=40, suprimido=True),
)


def _cand(enunciado, citados=("Q01-A",), qid="Q01-A", grupo="60+", metrica="rage_click", tipo="padrao_observado"):
    return Candidata(tipo=tipo, titulo="t", enunciado=enunciado,
                     evidencia_principal=RefEvidencia(query_id=qid, grupo=grupo, metrica=metrica),
                     query_ids_citados=list(citados), o_que_o_dado_responde="Quem e quanto.",
                     o_que_so_a_research_responde="Por quê.", pergunta_para_research="O que acontece?")


@pytest.mark.parametrize("texto", [
    "12,3% dos usuários", "12% dos usuários", "12.3% (ponto por engano)", "lift de 2,5x", "2,5 vezes mais",
    "1.234 usuários", "cerca de 45 mil usuários", "8 p.p. acima", "3 em cada 10 casos",
])
def test_numeros_com_fonte_na_consulta_citada(texto):
    v = verificar_candidata(_cand(texto), REG)
    assert v.verificada and not v.orfaos, v.orfaos


@pytest.mark.parametrize("texto", ["13% dos usuários", "46 mil usuários", "lift de 3,1", "4 em cada 10 casos"])
def test_numeros_sem_fonte_viram_orfaos(texto):
    v = verificar_candidata(_cand(texto), REG)
    assert not v.verificada and v.orfaos


def test_cerca_de_aceita_cinco_por_cento_e_milhar_com_ponto():
    assert verificar_candidata(_cand("cerca de 3.300 usuários (estimado)", citados=("Q02-A",), qid="Q02-A", grupo="18-24"), REG).verificada
    assert not verificar_candidata(_cand("3.300 usuários", citados=("Q02-A",), qid="Q02-A", grupo="18-24"), REG).verificada


def test_rotulos_e_identificadores_nao_sao_extraidos():
    texto = "Usuários 60+ e 18-24 no android 8.4.0 (Q01-A), janela de 10 min, IC95, desde 01/08 e 2026-08-30."
    rotulos = ["60+", "18-24", "android 8.4.0", "10 min"]
    assert extrair_numeros(texto, rotulos) == []


def test_numero_de_consulta_nao_citada_e_orfao_com_dica():
    v = verificar_candidata(_cand("77,7% dos usuários"), REG)
    assert not v.verificada and "Q02-A" in v.orfaos[0]
    assert verificar_candidata(_cand("77,7% dos usuários (Q02-A)"), REG).verificada  # citado no próprio texto


def test_referencias_invalidas_e_causalidade_no_enunciado():
    assert verificar_candidata(_cand("ok", qid="Q09-A"), REG).refs_invalidas
    assert verificar_candidata(_cand("ok", qid="Q03-A", grupo="android 8.4.0"), REG).refs_invalidas  # suprimido
    assert verificar_candidata(_cand("ok", metrica="dead_click"), REG).refs_invalidas
    v = verificar_candidata(_cand("Os 60+ travam porque a lista é longa."), REG)
    assert v.avisos_causais and v.verificada  # causalidade não é número órfão; vira Hipótese nos rótulos
