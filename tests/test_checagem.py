"""Checagem da entrada: bloqueia com mensagem clara, declara lacunas e classifica o palpite."""
from datetime import date

import pandas as pd
import pytest

from contexto.carregar import carregar_contexto
from entrada.checagem import checar_entrada, classificar_palpite
from gerador.gerar_dados import garantir_exemplo
from motor.carga import localizar_arquivos

CTX = carregar_contexto("cartoes")
JORNADA = "bloqueio_desbloqueio"
AGOSTO = (date(2026, 8, 1), date(2026, 8, 30))


def _checar(arquivos, periodo=AGOSTO, palpite="Acho que idosos travam na confirmação"):
    return checar_entrada(arquivos, CTX, JORNADA, *periodo, palpite)


def test_entrada_normal_passa(pasta_normal):
    r = _checar(localizar_arquivos(pasta_normal))
    assert r.pode_seguir and not r.bloqueios and not r.lacunas
    assert r.palpite_status == "especifico"
    assert any("tagueamento.csv: 1.080.387 linhas" in t for t in r.ok)


def test_incompleta_segue_e_declara_quem_como_lacuna(pasta_incompleta):
    r = _checar(localizar_arquivos(pasta_incompleta))
    assert r.pode_seguir and r.modo_comportamental
    assert any("não dá para dizer quem" in l for l in r.lacunas)


def test_incorreta_bloqueia_com_contagem_e_exemplo():
    r = _checar(localizar_arquivos(garantir_exemplo("incorreta")))
    assert not r.pode_seguir
    assert "timestamp inválido" in r.bloqueios[0] and "2026-08-32 25:61:00" in r.bloqueios[0]
    assert "8.333 de 100.000" in r.bloqueios[0]


def test_periodo_vazio_bloqueia_e_diz_de_quando_sao_os_dados(pasta_normal):
    r = _checar(localizar_arquivos(pasta_normal), periodo=(date(2026, 3, 1), date(2026, 3, 31)))
    assert not r.pode_seguir and "Período vazio" in r.bloqueios[0] and "01/08/2026 a 30/08/2026" in r.bloqueios[0]


def _csv(pasta, nome, linhas, colunas):
    pd.DataFrame(linhas, columns=colunas).to_csv(pasta / f"{nome}.csv", index=False)
    return pasta / f"{nome}.csv"


TAG = ["user_id_hash", "session_id", "timestamp", "event_name", "platform", "app_version"]


def test_sem_tagueamento_ou_sem_coluna_bloqueia(tmp_path):
    assert "obrigatório" in _checar({}).bloqueios[0]
    arq =_csv(tmp_path, "tagueamento", [("a1", "s1", "2026-08-10 10:00:00", "card_lock_start", "ios")], TAG[:-1])
    r = _checar({"tagueamento": arq})
    assert not r.pode_seguir and "app_version" in r.bloqueios[0]


def test_identificador_com_cara_de_dado_pessoal_bloqueia(tmp_path):
    linhas = [("maria@exemplo.com", "s1", "2026-08-10 10:00:00", "card_lock_start", "ios", "8.5.0"),
              ("12345678901", "s2", "2026-08-10 10:00:00", "card_lock_start", "ios", "8.5.0")]
    r = _checar({"tagueamento": _csv(tmp_path, "tagueamento", linhas, TAG)})
    assert not r.pode_seguir and "cara de dado pessoal" in r.bloqueios[0]


def test_perfil_com_coluna_proibida_bloqueia(tmp_path):
    tag = _csv(tmp_path, "tagueamento", [("a1", "s1", "2026-08-10 10:00:00", "card_lock_start", "ios", "8.5.0")], TAG)
    perfil = _csv(tmp_path, "perfil", [("a1", "60+", "Aurora", "ios", "5a+", "123.456.789-00")],
                  ["user_id_hash", "faixa_etaria", "segmento", "plataforma", "tempo_de_conta", "cpf"])
    r = _checar({"tagueamento": tag, "perfil": perfil})
    assert not r.pode_seguir and "cpf" in r.bloqueios[0] and "só faixas" in r.bloqueios[0]


def test_poucos_usuarios_e_eventos_desconhecidos_viram_aviso(tmp_path):
    linhas = [(f"u{i}", f"s{i}", "2026-08-10 10:00:00", "card_lock_start", "ios", "8.5.0") for i in range(20)]
    linhas.append(("u0", "s0", "2026-08-10 10:01:00", "home_view", "ios", "8.5.0"))
    r = _checar({"tagueamento": _csv(tmp_path, "tagueamento", linhas, TAG)})
    assert r.pode_seguir
    assert any("Só 20 usuários" in a for a in r.avisos) and any("home_view" in a for a in r.avisos)


@pytest.mark.parametrize("palpite, status", [
    ("Acho que idosos travam na confirmação", "especifico"),
    ("Acho que o botão de confirmar não responde no Android", "especifico"),
    ("o app é ruim", "vago"),
    ("", "sem_palpite"),
    (None, "sem_palpite"),
])
def test_classificacao_do_palpite(palpite, status):
    assert classificar_palpite(palpite, CTX, JORNADA) == status


def test_palpite_vago_traz_sugestoes(pasta_normal):
    r = _checar(localizar_arquivos(pasta_normal), palpite="o app é ruim")
    assert r.pode_seguir and r.palpite_status == "vago" and r.sugestoes_palpite
    assert any("Palpite vago" in a for a in r.avisos)


def test_amostra_commitada_tem_o_formato_certo():
    from gerador.gerar_dados import PASTA_AMOSTRA
    arquivos = localizar_arquivos(PASTA_AMOSTRA)
    assert set(arquivos) == {"tagueamento", "fullstory", "perfil", "nps"}
    r = _checar(arquivos)
    assert r.pode_seguir, r.bloqueios  # só avisa que são poucos usuários
