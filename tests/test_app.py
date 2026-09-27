"""Fluxo clicável do começo ao fim, com o AppTest do Streamlit (sem navegador e sem rede)."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _botao(at, rotulo):
    return next(b for b in at.button if b.label == rotulo)


def _modo(at, modo):
    next(r for r in at.radio if r.label == "Agente").set_value(modo)


def test_fluxo_completo_com_agente_roteirizado(pasta_normal):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Dossiê de Hipóteses"

    at.text_input[1].set_value("Acho que idosos travam na confirmação")  # [0] = nome da PM
    _modo(at, "roteirizado")
    _botao(at, "Checar entrada").click()
    at.run()
    assert not at.exception
    assert any("tagueamento.csv" in s.value for s in at.success)

    _botao(at, "Gerar dossiê").click()
    at.run()  # executa o pipeline e vai para o resultado
    assert not at.exception, at.exception
    textos = " ".join(m.value for m in at.markdown)
    assert "As 2 hipóteses mais fortes" in textos
    assert "android 8.4.0" in textos and "60+" in textos

    # A PM aceita a primeira hipótese: a decisão entra no registro e libera o pedido à Research
    at.segmented_control[0].set_value("aceitar")
    at.run()  # no navegador, escolher a decisão já dispara um rerun que habilita o botão
    _botao(at, "Registrar").click()
    at.run()
    assert not at.exception
    assert any("Decisão registrada" in i.value for i in at.info)


def test_entrada_incompleta_segue_e_declara_lacuna(pasta_incompleta):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.selectbox[1].set_value("incompleta")  # [0] = jornada
    _modo(at, "roteirizado")
    _botao(at, "Checar entrada").click()
    at.run()
    assert not at.exception
    assert any("não dá para dizer quem" in i.value for i in at.info)
    assert any("Modo comportamental" in w.value for w in at.warning)


class _ClienteQueFalha:
    """Simula o Devin estourando o tempo da rodada; conta quantas sessões foram abertas."""
    llm = "devin-v1"
    sessoes = 0

    def criar_sessao(self, prompt, **_):
        _ClienteQueFalha.sessoes += 1
        return "s-falha", ""

    def aguardar_rodada(self, *a, **k):
        from agente.cliente_devin import ErroDevin
        raise ErroDevin("timeout", "O Devin não concluiu a rodada 1 em 180 s.")

    def enviar_mensagem(self, *a, **k):
        pass

    def encerrar(self, *a, **k):
        pass


def test_erro_do_agente_nao_reexecuta_em_rerun_e_permite_tentar_de_novo(pasta_normal, monkeypatch):
    import ui.etapas
    monkeypatch.setattr(ui.etapas, "_cliente", lambda modo: _ClienteQueFalha())
    _ClienteQueFalha.sessoes = 0
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    _modo(at, "roteirizado")
    _botao(at, "Checar entrada").click()
    at.run()
    _botao(at, "Gerar dossiê").click()
    at.run()
    assert _ClienteQueFalha.sessoes == 1
    assert any("demorou mais que o limite" in e.value for e in at.error)
    at.run()  # um rerun qualquer só mostra o erro
    assert _ClienteQueFalha.sessoes == 1
    _botao(at, "Tentar de novo").click()
    at.run()
    assert _ClienteQueFalha.sessoes == 2


def test_cenario_incorreto_bloqueia_e_nao_deixa_gerar():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.selectbox[1].set_value("incorreta")
    _modo(at, "roteirizado")
    _botao(at, "Checar entrada").click()
    at.run()
    assert any("timestamp inválido" in e.value for e in at.error)
    assert _botao(at, "Gerar dossiê").disabled


def test_demo_gravada_usa_a_entrada_da_gravacao_e_roda_sem_rede(pasta_normal):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.text_input[1].set_value("outro palpite qualquer")
    _modo(at, "gravado")
    _botao(at, "Checar entrada").click()
    at.run()
    assert any("Demo gravada: reproduz a execução real do Devin" in i.value for i in at.info)
    assert at.session_state.entrada.palpite == "Acho que idosos travam na confirmação"  # entrada da gravação
    _botao(at, "Gerar dossiê").click()
    at.run()
    assert not at.exception, at.exception
    exe = at.session_state.execucao
    assert exe.dossie.meta.llm == "gravado"
    grupos = {h.candidata.evidencia_principal.grupo for h in exe.dossie.principais}
    assert grupos == {"android 8.4.0", "60+"}
