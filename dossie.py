"""Dossiê em Markdown (tela e download) e pedido à Research (simulado: gera o texto, não envia)."""
from __future__ import annotations

from contratos import DecisaoPM, Dossie, Hipotese, Rotulo
from motor.estatistica import fmt_int, fmt_num, fmt_pct

ICONE = {Rotulo.EVIDENCIA: "🟢", Rotulo.INDICIO: "🟡", Rotulo.HIPOTESE: "🔵", Rotulo.LACUNA: "⚪", Rotulo.DESCARTADA: "⚫"}
ACAO = {"aceitar": "Aceita", "editar": "Aceita com edição", "rejeitar": "Rejeitada", "mais_analise": "Pediu mais análise"}
CAMPOS_TEXTO = ("titulo", "enunciado", "o_que_o_dado_responde", "o_que_so_a_research_responde", "pergunta_para_research")


def decisao_de(d: Dossie, hipotese_id: str) -> DecisaoPM | None:
    return next((x for x in reversed(d.decisoes) if x.hipotese_id == hipotese_id), None)


def textos(h: Hipotese, decisao: DecisaoPM | None) -> dict[str, str]:
    """Textos finais: os da PM quando ela editou, senão os do agente."""
    base = {campo: getattr(h.candidata, campo) for campo in CAMPOS_TEXTO}
    if decisao and decisao.acao == "editar":
        base.update({k: v for k, v in decisao.textos_editados.items() if v})
    return base


def _ancora(qid: str) -> str:
    return f"[{qid}](#{qid.lower()})"


def _ic(ic: tuple[float, float] | None) -> str:
    return "—" if ic is None else f"{fmt_num(ic[0])}–{fmt_num(ic[1])}"


def quem(h: Hipotese) -> str:
    grupo = h.candidata.evidencia_principal.grupo
    if grupo is None or grupo == "todos":
        return "população toda (sem grupo de comparação)"
    la = h.linha_a
    comparacao = f": taxa {fmt_pct(la.taxa)} contra {fmt_pct(la.taxa_resto)} nos demais" if la and la.taxa_resto is not None else ""
    return f"{grupo} ({h.dimensao or 'grupo'}){comparacao}"


def quantos(h: Hipotese) -> str:
    if h.usuarios_afetados is None:
        return "—"
    base = f"{fmt_int(h.usuarios_afetados)} usuários no período"
    return base + (" (estimado: FullStory é amostra de 10%, ×10)" if h.extrapolado else "")


def resumo_verificacao(v) -> str:
    base = (f"os {len(v.conferidos)} números do texto têm fonte nas consultas citadas" if v.conferidos
            else "o texto não traz números; os números acima vêm direto das consultas")
    return base + (", depois de uma correção pedida pelo verificador" if v.refeita else "")


def bloco_hipotese(h: Hipotese, d: Dossie) -> str:
    decisao = decisao_de(d, h.id)
    t = textos(h, decisao)
    la, lb = h.linha_a, h.linha_b
    linhas = [
        f"### {h.id} · {t['titulo']}",
        f"{ICONE[h.rotulo]} **{h.rotulo.value}** · {h.motivo} _(regra {h.regra})_",
        "",
        t["enunciado"],
        "",
        f"- **Quantos:** {quantos(h)}",
        f"- **Quem:** {quem(h)}",
    ]
    if la and la.lift is not None:
        lift_b = f" · metade B {fmt_num(lb.lift)} (IC99 {_ic(lb.ic99)})" if lb and lb.lift is not None else ""
        linhas.append(f"- **Lift:** metade A {fmt_num(la.lift)} (IC95 {_ic(la.ic95)}){lift_b}")
    if la:
        linhas.append(f"- **n:** metade A {fmt_int(la.n)}" + (f" · metade B {fmt_int(lb.n)}" if lb else ""))
    fontes = [h.query_ids[k] for k in ("A", "B", "T") if k in h.query_ids]
    citados = [q for q in h.candidata.query_ids_citados if q not in fontes]
    linhas.append("- **Fontes:** " + " · ".join(_ancora(q) for q in fontes + citados))
    if h.evidencias_complementares:
        linhas.append("- **Evidências complementares:** " + "; ".join(h.evidencias_complementares))
    linhas += [
        f"- **O que o dado responde:** {t['o_que_o_dado_responde']}",
        f"- **O que só a Research responde:** {t['o_que_so_a_research_responde']}",
        f"- **Pergunta para a Research:** {t['pergunta_para_research']}",
    ]
    v = h.verificacao
    if v.verificada:
        linhas.append(f"- **Verificação:** ✅ {resumo_verificacao(v)}")
    else:
        linhas.append("- **Verificação:** ⚠️ não verificada: " + "; ".join(v.orfaos + v.refs_invalidas))
    if decisao:
        comentario = f": {decisao.comentario}" if decisao.comentario else ""
        linhas.append(f"- **Decisão da PM:** {ACAO[decisao.acao]} por {decisao.pm} em {decisao.ts}{comentario}")
    else:
        linhas.append("- **Decisão da PM:** pendente")
    return "\n".join(linhas)


def gerar_markdown(d: Dossie, nome_squad: str, nome_jornada: str, apendice: dict[str, str] | None = None) -> str:
    e, m = d.entrada, d.meta
    agente = {"devin-v1": "Devin (API v1)", "gravado": "execução gravada (modo demo)", "roteirizado": "agente roteirizado (teste)"}
    partes = [
        f"# Dossiê de hipóteses: {nome_jornada}",
        f"**{nome_squad}** · período {e.periodo_inicio:%d/%m/%Y} a {e.periodo_fim:%d/%m/%Y} · PM: {e.pm}",
        f"Execução `{m.run_id}` · agente: {agente.get(m.llm, m.llm)} · prompt {m.prompt_versao} (`{m.prompt_sha}`) · "
        f"contexto `{m.contexto_sha}` · dados `{m.dados_hash}`",
        "",
        *[f"> {aviso}" for aviso in d.avisos],
        "",
        "## Seu palpite",
        f"**Palpite:** {e.palpite}" if e.palpite else "**Palpite:** nenhum (exploração aberta)",
        "",
        d.resposta_ao_palpite or "_O agente não avaliou o palpite._",
        "",
        "## As 2 hipóteses mais fortes",
    ]
    if d.principais:
        for h in d.principais:
            partes += [bloco_hipotese(h, d), ""]
    else:
        partes += ["Nenhuma hipótese sustentada pelos dados nesta execução.", ""]

    outros = [("Outras evidências", d.outras_evidencias), ("Hipóteses para a Research", d.research),
              ("Indícios (não viram evidência)", d.indicios), ("Descartadas pelo código", d.descartadas)]
    if any(lista for _, lista in outros):
        partes.append("## Outros achados")
        for titulo, lista in outros:
            if lista:
                partes.append(f"**{titulo}**")
                for h in lista:
                    fonte = h.query_ids.get("A", "?")
                    partes.append(f"- {ICONE[h.rotulo]} {h.id} · {h.candidata.titulo}: {h.motivo} ({_ancora(fonte)})")
                partes.append("")
    if d.lacunas:
        partes += ["## Lacunas", *[f"- ⚪ {l}" for l in d.lacunas], ""]
    if d.proximos_passos:
        partes += ["## Próximos passos", *[f"- {p}" for p in d.proximos_passos], ""]
    if apendice:
        partes += ["## Apêndice: consultas", "Cada número do dossiê sai de uma destas consultas, executadas por código."]
        for qid, texto in apendice.items():
            partes += [f"### {qid}", "```", texto, "```"]
    return "\n".join(partes).strip() + "\n"


def gerar_pedido_research(d: Dossie, modelo: str, nome_squad: str, nome_jornada: str) -> str:
    """Pedido simulado: só as hipóteses que a PM aceitou (com ou sem edição)."""
    periodo = f"{d.entrada.periodo_inicio:%d/%m/%Y} a {d.entrada.periodo_fim:%d/%m/%Y}"
    blocos = []
    for h in d.principais + d.outras_evidencias + d.research + d.indicios:
        decisao = decisao_de(d, h.id)
        if not decisao or decisao.acao not in ("aceitar", "editar"):
            continue
        t = textos(h, decisao)
        blocos.append(modelo.strip().format(
            squad=nome_squad, jornada=nome_jornada, periodo=periodo, enunciado=t["enunciado"],
            o_que_o_dado_responde=t["o_que_o_dado_responde"], o_que_so_a_research_responde=t["o_que_so_a_research_responde"],
            pergunta_para_research=t["pergunta_para_research"], query_ids=", ".join(h.query_ids.values()),
            decisao=f"{ACAO[decisao.acao]} por {decisao.pm}" + (f" ({decisao.comentario})" if decisao.comentario else ""),
        ))
    return "\n\n---\n\n".join(blocos)
