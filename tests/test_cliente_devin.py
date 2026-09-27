import json

import pytest
import requests

from agente.cliente_devin import ClienteDevin, ErroDevin

CHAVE = "apk_teste_nao_real"


class Resposta:
    def __init__(self, status=200, corpo=None):
        self.status_code = status
        self._corpo = corpo
        self.text = "" if corpo is None else json.dumps(corpo)
        self.content = self.text.encode()

    def json(self):
        return self._corpo


class HttpFalso:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def request(self, metodo, url, **kwargs):
        self.chamadas.append((metodo, url, kwargs))
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.t += s


def _cliente(respostas):
    http, relogio = HttpFalso(respostas), Relogio()
    return ClienteDevin(CHAVE, base="https://x/v1", http=http, dormir=relogio.dormir, relogio=relogio), http


def _sessao(status, rodada=None):
    so = None if rodada is None else {"rodada": rodada, "fase": "plano"}
    return Resposta(200, {"session_id": "s1", "status_enum": status, "structured_output": so, "messages": []})


def test_sem_chave_da_erro_de_configuracao():
    with pytest.raises(ErroDevin) as e:
        ClienteDevin("")
    assert e.value.tipo == "config"


def test_401_vira_erro_de_autenticacao_sem_vazar_a_chave():
    cli, _ = _cliente([Resposta(401, {"detail": "invalid"})])
    with pytest.raises(ErroDevin) as e:
        cli.listar_sessoes()
    assert e.value.tipo == "autenticacao" and CHAVE not in str(e.value)


def test_falha_de_rede_vira_erro_de_rede():
    cli, _ = _cliente([requests.ConnectionError("x")])
    with pytest.raises(ErroDevin) as e:
        cli.listar_sessoes()
    assert e.value.tipo == "rede"


def test_criar_sessao_isola_conhecimento_e_segredos_da_organizacao():
    cli, http = _cliente([Resposta(200, {"session_id": "s1", "url": "https://app.devin.ai/sessions/s1"})])
    sid, url = cli.criar_sessao("oi", schema={"type": "object"}, titulo="t", tags=["a"], max_acu=2)
    corpo = http.chamadas[0][2]["json"]
    assert (sid, url) == ("s1", "https://app.devin.ai/sessions/s1")
    assert corpo["knowledge_ids"] == [] and corpo["secret_ids"] == [] and corpo["unlisted"] is True
    assert corpo["structured_output_schema"] == {"type": "object"} and corpo["max_acu_limit"] == 2


def test_aguardar_rodada_espera_status_parado_com_a_rodada_certa():
    cli, _ = _cliente([_sessao("working"), _sessao("working", 1), _sessao("blocked", 1)])
    est = cli.aguardar_rodada("s1", 1, intervalo_s=5)
    assert est.status_enum == "blocked" and est.rodada == 1


def test_parou_sem_publicar_recebe_uma_cutucada_e_depois_conclui():
    cli, http = _cliente([
        _sessao("working"), _sessao("blocked"), _sessao("blocked"),  # trabalhou e parou sem publicar
        Resposta(200, None),  # POST da cutucada
        _sessao("working"), _sessao("blocked", 1),
    ])
    est = cli.aguardar_rodada("s1", 1, cutucada="publique a rodada 1")
    assert est.rodada == 1
    assert [c[0] for c in http.chamadas].count("POST") == 1


def test_parou_de_novo_depois_da_cutucada_falha_com_formato():
    cli, _ = _cliente([
        _sessao("working"), _sessao("blocked"), _sessao("blocked"), Resposta(200, None),
        _sessao("working"), _sessao("blocked"), _sessao("blocked"),
    ])
    with pytest.raises(ErroDevin) as e:
        cli.aguardar_rodada("s1", 1, cutucada="publique")
    assert e.value.tipo == "formato"


def test_sessao_expirada_e_timeout():
    cli, _ = _cliente([_sessao("working"), _sessao("expired")])
    with pytest.raises(ErroDevin) as e:
        cli.aguardar_rodada("s1", 1)
    assert e.value.tipo == "sessao_encerrada"

    cli, _ = _cliente([_sessao("working")] * 10)
    with pytest.raises(ErroDevin) as e:
        cli.aguardar_rodada("s1", 1, timeout_s=20, intervalo_s=5)
    assert e.value.tipo == "timeout"


def test_rodada_como_texto_e_mensagens_do_agente():
    from agente.cliente_devin import EstadoSessao
    est = EstadoSessao("s1", "blocked", "blocked", {"rodada": "2"}, [
        {"type": "initial_user_message", "message": "prompt"},
        {"type": "devin_message", "message": "Rodada 2 publicada."},
    ], {})
    assert est.rodada == 2
    assert est.mensagens_do_agente() == ["Rodada 2 publicada."]
    assert EstadoSessao("s1", "blocked", None, {"rodada": True}, [], {}).rodada is None
