"""Modo demo: gravar uma execução e reproduzi-la sem rede, com as consultas rodando de verdade."""
from datetime import date

import pytest

from agente.cliente_devin import ClienteRoteirizado
from agente.gravacao import ARQUIVO_DEMO, ClienteGravado, carregar, gravacao_de
from agente.prompts import prompt_sha
from agente.roteiros import roteiro_gabarito
from contexto.carregar import carregar_contexto
from contratos import EntradaPM
from entrada.checagem import checar_entrada
from gabarito import conferir
from motor.carga import localizar_arquivos
from pipeline import rodar_execucao

CTX = carregar_contexto("cartoes")


def _rodar(pasta, cliente, entrada: EntradaPM):
    chk = checar_entrada(localizar_arquivos(pasta), CTX, entrada.jornada, entrada.periodo_inicio, entrada.periodo_fim,
                         entrada.palpite)
    return rodar_execucao(entrada, chk, CTX, cliente)


ENTRADA = EntradaPM(squad="cartoes", jornada="bloqueio_desbloqueio", periodo_inicio=date(2026, 8, 1),
                    periodo_fim=date(2026, 8, 30), palpite="Acho que idosos travam na confirmação", pm="PM teste",
                    fonte_dados="exemplo:normal")


def _resumo(exe):
    d = exe.dossie
    return [(h.id, h.rotulo.value, h.candidata.evidencia_principal.grupo, h.candidata.evidencia_principal.metrica)
            for h in d.principais + d.research + d.indicios + d.descartadas]


def test_gravar_e_reproduzir_da_o_mesmo_dossie(pasta_normal):
    original = _rodar(pasta_normal, ClienteRoteirizado(roteiro_gabarito), ENTRADA)
    gravacao = gravacao_de(original)
    assert [r["rodada"] for r in gravacao["rodadas"]] == [1, 2]
    replay = _rodar(pasta_normal, ClienteGravado(gravacao, atraso_s=0), ENTRADA)
    assert replay.erro is None and replay.dossie.meta.llm == "gravado"
    assert _resumo(replay) == _resumo(original)


def test_replay_recusa_dados_diferentes(pasta_normal):
    gravacao = gravacao_de(_rodar(pasta_normal, ClienteRoteirizado(roteiro_gabarito), ENTRADA))
    exe = _rodar(pasta_normal, ClienteGravado({**gravacao, "dados_hash": "outro"}, atraso_s=0), ENTRADA)
    assert exe.erro_tipo == "gravacao" and "outros dados" in exe.erro


def test_demo_commitada_reproduz_o_gabarito_offline(pasta_normal):
    gravacao = carregar(ARQUIVO_DEMO)
    if gravacao is None:
        pytest.skip("demo/execucao_gravada.json ainda não foi gravada (scripts/gravar_demo.py)")
    assert gravacao["prompt_sha"] == prompt_sha(), "procedimento mudou depois da gravação: regrave a demo"
    entrada = EntradaPM(**gravacao["entrada"])
    exe = _rodar(pasta_normal, ClienteGravado(gravacao, atraso_s=0), entrada)
    assert exe.erro is None
    assert all(i.ok for i in conferir(exe.dossie, tem_perfil=True))
