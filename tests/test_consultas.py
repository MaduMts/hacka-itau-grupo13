"""Consultas sobre uma base pequena montada à mão, com respostas conhecidas."""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from contexto.carregar import carregar_contexto
from contratos import Filtro, PedidoConsulta
from motor import consultas
from motor.carga import abrir_brutos, preparar_base
from motor.registro_consultas import RegistroConsultas

FUNIL = ["card_lock_start", "card_select", "lock_reason_select", "lock_confirm"]


def _pedido(**kw):
    base = dict(segmentar_por="nenhum", filtros=[], pagina=None, tipo_atrito=None, evento_a=None, evento_b=None,
                janela_min=None, motivo="teste")
    base.update(kw)
    return PedidoConsulta(**base)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """80 usuários android 8.4.0 (20 abandonam na confirmação, 30 dão dead click), 80 ios 8.5.0
    (4 abandonam, 3 dead clicks) e 40 android 8.6.0-beta (grupo pequeno, suprimido)."""
    pasta = tmp_path_factory.mktemp("dados")
    t0 = datetime(2026, 8, 10, 12, 0, 0)
    tag, fs = [], []
    grupos = [("android", "8.4.0", 80, 20, 30), ("ios", "8.5.0", 80, 4, 3), ("android", "8.6.0-beta", 40, 2, 1)]
    for plat, ver, n, abandono, dead in grupos:
        for i in range(n):
            uid, sid = f"{ver}-{i}", f"s-{ver}-{i}"
            eventos = FUNIL if i >= abandono else FUNIL[:3]
            eventos = eventos + (["lock_success"] if i >= abandono else [])
            for k, ev in enumerate(eventos):
                tag.append((uid, sid, (t0 + timedelta(seconds=10 * k)).isoformat(sep=" "), ev, plat, ver))
            fs.append((sid, uid, t0.isoformat(sep=" "), "confirmacao_bloqueio", "page_view", "", 5.0))
            if i < dead:
                fs.append((sid, uid, t0.isoformat(sep=" "), "confirmacao_bloqueio", "dead_click", "btn_confirmar_bloqueio", None))
    pd.DataFrame(tag, columns=["user_id_hash", "session_id", "timestamp", "event_name", "platform", "app_version"]).to_csv(
        pasta / "tagueamento.csv", index=False)
    pd.DataFrame(fs, columns=["session_id", "user_id_hash", "timestamp", "page", "event_type", "element", "time_on_page_s"]).to_csv(
        pasta / "fullstory.csv", index=False)
    arquivos = {"tagueamento": pasta / "tagueamento.csv", "fullstory": pasta / "fullstory.csv"}
    return preparar_base(abrir_brutos(arquivos), arquivos, carregar_contexto("cartoes"), "bloqueio_desbloqueio",
                         date(2026, 8, 1), date(2026, 8, 30))


def _linha(res, grupo, metrica):
    return next(l for l in res.linhas if l.grupo == grupo and l.metrica == metrica)


def test_funil_na_base_toda_bate_com_a_contagem(base):
    res = consultas.executar(base, _pedido(ferramenta="funil", segmentar_por="versao_app"), "T", "Q01-T")
    l = _linha(res, "android 8.4.0", "abandono:lock_confirm")
    assert (l.n, l.casos) == (80, 20)
    assert (l.n_resto, l.casos_resto) == (120, 6)
    assert l.lift == pytest.approx((20 / 80) / (6 / 120))
    assert _linha(res, "android 8.6.0-beta", "abandono:lock_confirm").suprimido
    assert "abandono:lock_success" not in {l.metrica for l in res.linhas}  # repetiria o erro


def test_atrito_conta_usuarios_com_visita_e_elemento_principal(base):
    res = consultas.executar(base, _pedido(ferramenta="atrito", pagina="confirmacao_bloqueio", tipo_atrito="dead_click",
                                           segmentar_por="versao_app"), "T", "Q02-T")
    l = _linha(res, "android 8.4.0", "dead_click")
    assert (l.n, l.casos) == (80, 30) and l.casos_extrapolados == 300
    assert l.extras["elemento"] == "btn_confirmar_bloqueio"
    assert "suprimidos (n<50, sem números): android 8.6.0-beta" in consultas.texto_para_agente(res)


def test_validacao_explica_o_que_e_valido(base):
    with pytest.raises(consultas.ErroConsulta, match="Valores válidos"):
        consultas.validar(base, _pedido(ferramenta="funil", filtros=[Filtro(dimensao="versao_app", valor="9.9.9")]))
    with pytest.raises(consultas.ErroConsulta, match="indisponível"):
        consultas.validar(base, _pedido(ferramenta="funil", segmentar_por="faixa_etaria"))  # sem perfil
    with pytest.raises(consultas.ErroConsulta, match="(?i)no máximo"):
        consultas.validar(base, _pedido(ferramenta="funil", filtros=[Filtro(dimensao="plataforma", valor="ios"),
                                                                      Filtro(dimensao="versao_app", valor="ios 8.5.0")]))
    with pytest.raises(consultas.ErroConsulta, match="não pertence"):
        consultas.validar(base, _pedido(ferramenta="atrito", pagina="home", tipo_atrito="dead_click"))


def test_registro_numera_na_ordem_e_replica_a_mesma_especificacao(base):
    reg = RegistroConsultas(base)
    assert reg.panorama().query_id == "Q00-A"
    a = reg.executar(_pedido(ferramenta="funil", segmentar_por="versao_app"))
    b = reg.executar(_pedido(ferramenta="atrito", pagina="confirmacao_bloqueio", tipo_atrito="dead_click"))
    assert (a.query_id, b.query_id) == ("Q01-A", "Q02-A")
    rep = reg.replicar("Q01-A", "B")
    assert rep.query_id == "Q01-B" and rep.params == a.params and rep.metade == "B"
    assert reg.replicar("Q01-A", "B") is rep  # cache
    assert reg.linha("Q02-A", None, "dead_click").grupo == "todos"
