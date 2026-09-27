"""Governança e bordas pela interface (AppTest, sem rede): decisões da PM, registro, upload e memória."""
import json
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from agente.cliente_devin import ClienteRoteirizado
from agente.roteiros import roteiro_gabarito
from contexto.carregar import carregar_contexto
from contratos import EntradaPM
from entrada.checagem import checar_entrada
from gerador.gerar_dados import PASTA_AMOSTRA
from motor.carga import localizar_arquivos
from pipeline import rodar_execucao

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _botao(at, rotulo):
    return next(b for b in at.button if b.label == rotulo)


def _modo(at, modo):
    next(r for r in at.radio if r.label == "Agente").set_value(modo)


def _ate_o_resultado(cenario="normal", palpite="Acho que idosos travam na confirmação"):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.selectbox[1].set_value(cenario)
    at.text_input[1].set_value(palpite)
    _modo(at, "roteirizado")
    _botao(at, "Checar entrada").click()
    at.run()
    _botao(at, "Gerar dossiê").click()
    at.run()
    assert not at.exception, at.exception
    return at


def _decidir(at, hipotese, acao, comentario="", edicoes=None):
    at.segmented_control(key=f"acao-{hipotese}").set_value(acao)
    at.run()
    for campo, texto in (edicoes or {}).items():
        at.text_area(key=f"ed-{hipotese}-{campo}").set_value(texto)
    if comentario:
        at.text_input(key=f"com-{hipotese}").set_value(comentario)
    at.button(key=f"reg-{hipotese}").click()
    at.run()
    assert not at.exception, at.exception


def _registro(at):
    return at.session_state.execucao.registro.ler()


def test_decisoes_e_pedido_a_research_ficam_no_registro_com_quem_fez(pasta_normal):
    at = _ate_o_resultado()
    _decidir(at, "H1", "aceitar", comentario="Priorizar correção do botão na 8.4.0")
    _decidir(at, "H2", "rejeitar", comentario="Já em investigação")
    _botao(at, "Encaminhar à Research (simulado)").click()
    at.run()
    assert any("simulação: nada saiu do protótipo" in s.value for s in at.success)
    eventos = _registro(at)
    decisoes = [e for e in eventos if e["evento"] == "decisao"]
    assert [(e["ator"], e["dados"]["hipotese_id"], e["dados"]["acao"]) for e in decisoes] == [
        ("pm", "H1", "aceitar"), ("pm", "H2", "rejeitar")]
    pedido = next(e for e in eventos if e["evento"] == "pedido_research_simulado")
    assert pedido["ator"] == "pm" and "Priorizar correção" in pedido["dados"]["pedido"]
    assert "Já em investigação" not in pedido["dados"]["pedido"]  # só o que a PM aceitou
    atores = {e["ator"] for e in eventos}
    assert {"sistema", "codigo", "agente", "verificador", "pm"} <= atores


def test_edicao_com_numero_sem_fonte_avisa_e_guarda_o_original(pasta_normal):
    at = _ate_o_resultado()
    original = at.session_state.execucao.dossie.principais[0].candidata.enunciado
    _decidir(at, "H1", "editar", edicoes={"enunciado": "Quase todos (93,7%) não conseguem confirmar."})
    assert any("A edição trouxe números sem fonte" in w.value for w in at.warning)
    evento = next(e for e in _registro(at) if e["evento"] == "decisao")
    assert evento["dados"]["textos_originais"]["enunciado"] == original
    assert evento["dados"]["numeros_sem_fonte_na_edicao"]


def test_pedir_mais_analise_gera_execucao_filha(pasta_normal):
    at = _ate_o_resultado()
    pai = at.session_state.execucao.run_id
    _decidir(at, "H1", "mais_analise", comentario="Ver se o abandono na confirmação muda por segmento")
    assert at.session_state.etapa == "checagem"
    _botao(at, "Gerar dossiê").click()
    at.run()
    assert not at.exception
    filha = at.session_state.execucao
    assert filha.run_id != pai and filha.entrada.palpite.startswith("Ver se o abandono")
    inicio = filha.registro.ler()[0]
    assert inicio["evento"] == "execucao_iniciada" and inicio["dados"]["parent_run_id"] == pai


def test_sem_perfil_lista_proximos_passos_e_pede_acesso_simulado(pasta_incompleta):
    at = _ate_o_resultado(cenario="incompleta")
    assert any("Próximos passos (sem perfil do cliente)" in m.value for m in at.markdown)
    _botao(at, "Solicitar acesso ao perfil ao dono do dado (simulado)").click()
    at.run()
    pedido = next(e for e in _registro(at) if e["evento"] == "acesso_perfil_solicitado_simulado")
    assert pedido["ator"] == "pm" and pedido["dados"]["dono"] == "Domínio Cliente (fictício)"
    assert any("simulação: nada foi enviado" in s.value for s in at.success)


def test_palpite_vago_mostra_sugestoes_e_deixa_seguir():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.text_input[1].set_value("o app é ruim")
    _modo(at, "roteirizado")
    _botao(at, "Checar entrada").click()
    at.run()
    assert any("Palpite vago" in w.value for w in at.warning)
    assert any("Palpites mais úteis" in m.value for m in at.markdown)
    assert not _botao(at, "Gerar dossiê").disabled


def test_upload_das_amostras_passa_pela_checagem():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.radio[0].set_value("upload")
    _modo(at, "roteirizado")
    for i, nome in enumerate(("tagueamento", "fullstory", "perfil", "nps")):
        at.file_uploader[i].set_value((f"{nome}.csv", (PASTA_AMOSTRA / f"{nome}.csv").read_bytes(), "text/csv"))
    _botao(at, "Checar entrada").click()
    at.run()
    assert not at.exception
    assert at.session_state.fonte == "upload"
    assert any("tagueamento.csv:" in s.value for s in at.success)
    assert any("Só " in w.value and "usuários" in w.value for w in at.warning)  # amostra pequena: avisa, não bloqueia


def test_memoria_da_squad_chega_ao_agente_na_execucao_seguinte(pasta_normal):
    ctx = carregar_contexto("cartoes")
    entrada = EntradaPM(squad="cartoes", jornada="bloqueio_desbloqueio", periodo_inicio=date(2026, 8, 1),
                        periodo_fim=date(2026, 8, 30), palpite=None, pm="PM teste", fonte_dados="exemplo:normal")

    def rodar():
        chk = checar_entrada(localizar_arquivos(pasta_normal), ctx, "bloqueio_desbloqueio", date(2026, 8, 1),
                             date(2026, 8, 30), None)
        return rodar_execucao(entrada, chk, ctx, ClienteRoteirizado(roteiro_gabarito))

    primeira = rodar()
    h1 = primeira.dossie.principais[0]
    primeira.registro.evento("pm", "decisao", hipotese_id=h1.id, titulo=h1.candidata.titulo, rotulo=h1.rotulo.value,
                             acao="aceitar", comentario="Correção entra na próxima sprint")
    segunda = rodar()
    prompt = segunda.agente.textos_enviados[0]["texto"]
    assert "Decisões recentes da PM nesta squad" in prompt and h1.candidata.titulo in prompt
    assert any("Correção entra na próxima sprint" in m for m in segunda.dossie.memoria)
    evento = next(e for e in segunda.registro.ler() if e["evento"] == "memoria_da_squad")
    assert json.dumps(evento["dados"], ensure_ascii=False).count("próxima sprint") == 1
