"""Pipeline completo com o agente roteirizado (sem rede) sobre o dataset de exemplo."""
from datetime import date

import pytest

from agente.cliente_devin import ClienteRoteirizado
from agente.roteiros import roteiro_gabarito
from contexto.carregar import carregar_contexto
from contratos import EntradaPM, Rotulo
from entrada.checagem import checar_entrada
from motor.carga import localizar_arquivos
from pipeline import rodar_execucao

CTX = carregar_contexto("cartoes")


def _executar(pasta, roteiro=roteiro_gabarito, palpite="Acho que idosos travam na confirmação"):
    chk = checar_entrada(localizar_arquivos(pasta), CTX, "bloqueio_desbloqueio", date(2026, 8, 1), date(2026, 8, 30), palpite)
    entrada = EntradaPM(squad="cartoes", jornada="bloqueio_desbloqueio", periodo_inicio=date(2026, 8, 1),
                        periodo_fim=date(2026, 8, 30), palpite=palpite, pm="PM teste", fonte_dados="exemplo:teste")
    return rodar_execucao(entrada, chk, CTX, ClienteRoteirizado(roteiro))


def _grupos(hipoteses):
    return {h.candidata.evidencia_principal.grupo: h for h in hipoteses}


@pytest.fixture(scope="module")
def normal(pasta_normal):
    return _executar(pasta_normal)


def test_gabarito_padroes_1_e_2_viram_evidencia(normal):
    assert normal.erro is None
    principais = _grupos(normal.dossie.principais)
    assert set(principais) == {"android 8.4.0", "60+"}
    assert all(h.rotulo == Rotulo.EVIDENCIA and h.verificacao.verificada for h in principais.values())
    assert principais["android 8.4.0"].extrapolado  # FullStory: afetados estimados ×10


def test_gabarito_armadilhas_nao_viram_evidencia(normal):
    d = normal.dossie
    assert _grupos(d.descartadas)["Aurora"].regra == "R3"  # taxa-base: volume sem lift
    beta = _grupos(d.indicios + d.descartadas + d.principais)["android 8.6.0-beta"]
    assert beta.rotulo == Rotulo.INDICIO and beta.regra == "R4"  # amostra pequena
    evidencias = [h for h in d.principais + d.outras_evidencias if h.rotulo == Rotulo.EVIDENCIA]
    assert {h.candidata.evidencia_principal.grupo for h in evidencias} == {"android 8.4.0", "60+"}


def test_registro_guarda_quem_fez_o_que(normal):
    eventos = [e["evento"] for e in normal.registro.ler()]
    for esperado in ("execucao_iniciada", "entrada_checada", "sessao_criada", "rodada_publicada", "consulta_executada",
                     "verificacao", "rotulos", "dossie_gerado", "sessao_encerrada"):
        assert esperado in eventos, esperado
    inicio = normal.registro.ler()[0]["dados"]
    assert inicio["prompt_sha"] and inicio["contexto_sha"] == CTX.sha and inicio["llm"] == "roteirizado"


def test_sem_perfil_segue_declarando_a_lacuna(pasta_incompleta):
    exe = _executar(pasta_incompleta)
    assert exe.erro is None
    assert any("não dá para dizer quem" in l for l in exe.dossie.lacunas)
    principais = _grupos(exe.dossie.principais)
    assert principais["android 8.4.0"].rotulo == Rotulo.EVIDENCIA
    assert "60+" not in _grupos(exe.dossie.principais + exe.dossie.indicios + exe.dossie.descartadas)


def _roteiro_com_orfao(corrigir: bool):
    def roteiro(rodada, textos, registro):
        saida = roteiro_gabarito(rodada, textos, registro)
        if rodada >= 2 and (rodada == 2 or not corrigir):
            saida["candidatas"][0]["enunciado"] += " Isso atinge 999% dos usuários."
        return saida
    return roteiro


def test_numero_orfao_pede_uma_correcao_e_depois_passa(pasta_normal):
    exe = _executar(pasta_normal, _roteiro_com_orfao(corrigir=True))
    assert "problemas_encontrados" in [e["evento"] for e in exe.registro.ler()]
    assert all(h.verificacao.verificada for h in exe.dossie.principais)
    assert any(h.verificacao.refeita for h in exe.dossie.principais)


def test_numero_orfao_que_persiste_rebaixa_e_marca_o_texto(pasta_normal):
    exe = _executar(pasta_normal, _roteiro_com_orfao(corrigir=False))
    d = exe.dossie
    marcada = next(h for h in d.principais + d.outras_evidencias + d.indicios if "999%" in h.candidata.enunciado)
    assert not marcada.verificacao.verificada and marcada.rotulo != Rotulo.EVIDENCIA
    assert "⟦sem fonte⟧" in marcada.candidata.enunciado


def test_gabarito_padrao_3_vira_hipotese_para_a_research(normal):
    p3 = next(h for h in normal.dossie.research if h.candidata.evidencia_principal.metrica == "sequencia")
    assert p3.rotulo == Rotulo.HIPOTESE and p3.regra == "R2" and p3.familia == "comportamental"
    assert p3.usuarios_afetados and p3.usuarios_afetados > 15_000  # ~14% de quem bloqueou


def test_nps_entra_so_como_pista_complementar(normal):
    p2 = _grupos(normal.dossie.principais)["60+"]
    assert any("tema:dificil_escolher_motivo" in c and "Indício" in c for c in p2.evidencias_complementares)


def test_conferencia_do_gabarito_fica_toda_verde(normal, pasta_incompleta):
    from gabarito import conferir
    itens = conferir(normal.dossie, tem_perfil=True)
    assert [i.id for i in itens] == ["P1", "P2", "P3", "T4", "T5"]
    assert all(i.ok for i in itens), [(i.id, i.encontrado) for i in itens if not i.ok]
    sem_perfil = conferir(_executar(pasta_incompleta).dossie, tem_perfil=False)
    assert all(i.ok for i in sem_perfil) and next(i for i in sem_perfil if i.id == "P2").esperado.startswith("Lacuna")


def test_injecao_no_nps_nunca_chega_ao_agente(normal, pasta_normal):
    assert "IGNORE AS INSTRUÇÕES" in (pasta_normal / "nps.csv").read_text(encoding="utf-8")  # está nos dados
    enviados = " ".join(t["texto"] for t in normal.agente.textos_enviados)
    assert "IGNORE" not in enviados and "Toquei em confirmar" not in enviados
    assert all("IGNORE" not in r.texto_llm for r in normal.consultas.resultados.values())
