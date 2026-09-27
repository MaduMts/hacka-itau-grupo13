import pytest

from contexto.carregar import carregar_contexto, listar_squads


def test_squad_padrao_carrega_com_jornada_completa():
    ctx = carregar_contexto("cartoes")
    assert "cartoes" in listar_squads()
    j = ctx.jornada("bloqueio_desbloqueio")
    assert j["eventos_funil"][0] == "card_lock_start" and j["eventos_funil"][-1] == "lock_success"
    assert ctx.pares_reversiveis("bloqueio_desbloqueio") == [("lock_success", "unlock_success")]


def test_todo_evento_e_pagina_da_jornada_esta_no_dicionario():
    ctx = carregar_contexto("cartoes")
    dicionario = ctx.squad["dicionario_eventos"]
    for ev in ctx.eventos("bloqueio_desbloqueio"):
        assert ev in dicionario, ev
        assert dicionario[ev]["pagina"] in ctx.paginas("bloqueio_desbloqueio")
    for pg in ctx.paginas("bloqueio_desbloqueio"):
        assert pg in ctx.squad["paginas"], pg


def test_texto_para_agente_e_deterministico_e_traz_politicas():
    a = carregar_contexto("cartoes")
    b = carregar_contexto("cartoes")
    texto = a.texto_para_agente("bloqueio_desbloqueio")
    assert texto == b.texto_para_agente("bloqueio_desbloqueio")
    assert a.sha == b.sha and len(a.sha) == 16
    assert "Correlação não é causa" in texto
    assert "card_lock_start → card_select" in texto
    assert "android 8.4.0 (22/07/2026)" in texto
    assert "n/a (dados sintéticos)" not in texto  # IDs de LIA/RIPD vão só para o registro


def test_erros_claros_para_squad_ou_jornada_inexistente():
    with pytest.raises(KeyError, match="não encontrada"):
        carregar_contexto("nao_existe")
    with pytest.raises(KeyError, match="Válidas"):
        carregar_contexto("cartoes").jornada("pix")
