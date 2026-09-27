"""Peças visuais reutilizadas pelas telas."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from contratos import Hipotese, ResultadoConsulta, Rotulo
from dossie import quantos, quem
from motor.consultas import descrever_params
from motor.estatistica import fmt_int, fmt_num, fmt_pct

COR = {Rotulo.EVIDENCIA: "green", Rotulo.INDICIO: "orange", Rotulo.HIPOTESE: "blue", Rotulo.LACUNA: "gray",
       Rotulo.DESCARTADA: "gray"}
ICONE_ROTULO = {Rotulo.EVIDENCIA: ":material/verified:", Rotulo.INDICIO: ":material/help:",
                Rotulo.HIPOTESE: ":material/lightbulb:", Rotulo.LACUNA: ":material/block:",
                Rotulo.DESCARTADA: ":material/do_not_disturb_on:"}


def selo(rotulo: Rotulo) -> None:
    st.badge(rotulo.value, icon=ICONE_ROTULO[rotulo], color=COR[rotulo])


def tabela_resultado(res: ResultadoConsulta) -> pd.DataFrame:
    linhas = []
    for l in res.linhas:
        if l.suprimido:
            linhas.append({"grupo": l.grupo, "métrica": l.metrica, "n": "<50", "casos": "", "taxa": "", "demais": "",
                           "lift": "", "IC95": "", "% dos casos": "", "suprimido": "sim"})
            continue
        linhas.append({
            "grupo": l.grupo, "métrica": l.metrica, "n": fmt_int(l.n), "casos": fmt_int(l.casos),
            "taxa": fmt_pct(l.taxa), "demais": fmt_pct(l.taxa_resto),
            "lift": "—" if l.lift is None else fmt_num(l.lift),
            "IC95": "—" if l.ic95 is None else f"{fmt_num(l.ic95[0])}–{fmt_num(l.ic95[1])}",
            "% dos casos": fmt_pct(l.participacao), "suprimido": "",
        })
    return pd.DataFrame(linhas)


@st.dialog("Consulta executada por código", width="large")
def dialogo_consulta(res: ResultadoConsulta) -> None:
    st.markdown(f"**{res.query_id}** · `{res.ferramenta}` · {descrever_params(res.params)} · metade **{res.metade}**")
    metade = {"A": "metade A (exploração do agente)", "B": "metade B (réplica feita pelo código; o agente não viu)",
              "T": "base toda (para contar quantos são afetados)"}
    st.caption(f"{metade[res.metade]} · fonte: {res.fonte} · {res.duracao_ms:.0f} ms")
    if res.linhas:
        st.dataframe(tabela_resultado(res), hide_index=True)
    for aviso in res.avisos:
        st.info(aviso, icon=":material/info:")
    with st.expander("Texto exato que o agente leu"):
        st.code(res.texto_llm or "(consulta de réplica: o agente não recebe)", language=None)
    if res.sql:
        with st.expander("SQL executado"):
            st.code(res.sql, language="sql")


def metricas_hipotese(h: Hipotese) -> None:
    la, lb = h.linha_a, h.linha_b
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Usuários afetados", "—" if h.usuarios_afetados is None else fmt_int(h.usuarios_afetados),
              help=quantos(h))
    c2.metric("Lift metade A", "—" if not la or la.lift is None else fmt_num(la.lift),
              help="Taxa do grupo ÷ taxa dos demais, na metade que o agente explorou.")
    c3.metric("Lift metade B", "—" if not lb or lb.lift is None else fmt_num(lb.lift),
              help="Réplica feita pelo código na metade que o agente não viu.")
    c4.metric("n (A / B)", "—" if not la else f"{fmt_int(la.n)} / {fmt_int(lb.n) if lb else '—'}")
    st.caption(f"**Quem:** {quem(h)}")
