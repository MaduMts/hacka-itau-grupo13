import pandas as pd
import pytest

from gerador import padroes as P
from gerador.gerar_dados import gerar


def test_mesma_semente_gera_os_mesmos_dados():
    a, b = gerar(n_usuarios=5000), gerar(n_usuarios=5000)
    for nome in ("tagueamento", "fullstory", "perfil"):
        pd.testing.assert_frame_equal(getattr(a, nome), getattr(b, nome))
    assert not gerar(semente=1, n_usuarios=5000).tagueamento.equals(a.tagueamento)


@pytest.fixture(scope="module")
def dados():
    return gerar()


def test_volumes_do_readme(dados):
    assert len(dados.perfil) == 200_000
    assert 0.95e6 <= len(dados.tagueamento) <= 1.2e6
    sessoes = dados.tagueamento.session_id.nunique()
    assert abs(dados.fullstory.session_id.nunique() / sessoes - P.FULLSTORY_AMOSTRA) < 0.005
    assert dados.tagueamento.timestamp.min() >= pd.Timestamp(P.INICIO)
    assert dados.tagueamento.timestamp.max() <= pd.Timestamp(P.FIM)
    assert dados.tagueamento.user_id_hash.str.fullmatch(r"[0-9a-f]{16}").all()


def _taxa_atrito(dados, pagina, tipo, mascara_usuarios):
    fs = dados.fullstory[dados.fullstory.page == pagina]
    viu = set(fs[fs.event_type == "page_view"].user_id_hash)
    teve = set(fs[fs.event_type == tipo].user_id_hash)
    alvo = set(dados.perfil.user_id_hash[mascara_usuarios])
    return len(viu & teve & alvo) / len(viu & alvo)


def test_padroes_plantados_aparecem_com_a_forca_esperada(dados):
    tg, perfil = dados.tagueamento, dados.perfil
    versao = tg.drop_duplicates("user_id_hash").set_index("user_id_hash")
    versao = (versao.platform.astype(str) + " " + versao.app_version.astype(str)).reindex(perfil.user_id_hash).values
    p1 = versao == "android 8.4.0"
    assert _taxa_atrito(dados, "confirmacao_bloqueio", "dead_click", p1) > 0.30
    assert _taxa_atrito(dados, "confirmacao_bloqueio", "dead_click", ~p1) < 0.05
    idosos = (perfil.faixa_etaria == "60+").values
    assert _taxa_atrito(dados, "motivo_bloqueio", "rage_click", idosos) > 0.10
    assert _taxa_atrito(dados, "motivo_bloqueio", "rage_click", ~idosos) < 0.04

    ev = tg.pivot_table(index="user_id_hash", columns="event_name", values="session_id", aggfunc="count", observed=False).fillna(0)
    beta = set(perfil.user_id_hash[versao == "android 8.6.0-beta"])
    confirmaram = ev[ev.lock_confirm > 0]
    taxa_beta = (confirmaram.loc[confirmaram.index.isin(beta), "lock_error"] > 0).mean()
    assert taxa_beta > 0.35 and len(beta) < 400  # armadilha 5: taxa altíssima, n pequeno


def test_desbloqueio_rapido_em_cerca_de_14_por_cento_dos_bloqueios(dados):
    tg = dados.tagueamento.sort_values("timestamp")
    sucesso = tg[tg.event_name == "lock_success"][["session_id", "timestamp"]]
    desbloqueio = tg[tg.event_name == "unlock_success"][["session_id", "timestamp"]]
    juntos = sucesso.merge(desbloqueio, on="session_id", suffixes=("_bloq", "_desb"))
    rapidos = ((juntos.timestamp_desb - juntos.timestamp_bloq) <= pd.Timedelta(minutes=10)).sum()
    assert 0.12 < rapidos / len(sucesso) < 0.16


def test_nps_tem_cinco_por_cento_de_respondentes_e_uma_injecao(dados):
    assert 0.045 < len(dados.nps) / len(dados.perfil) < 0.055
    assert dados.nps.score.between(0, 10).all()
    assert dados.nps.comentario.str.startswith("IGNORE AS INSTRUÇÕES").sum() == 1
