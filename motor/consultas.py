"""Catálogo de consultas parametrizadas: o agente escolhe, o código calcula.

Cada consulta devolve linhas agregadas (grupo × métrica) com lift, IC e supressão de grupos
pequenos, mais um texto compacto para o agente (`texto_para_agente`). Não existe SQL livre:
dimensões vêm de uma lista fechada e valores de filtro entram como parâmetros.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable

from config import FATOR_FULLSTORY, MAX_FILTROS, N_MIN_EVIDENCIA
from contratos import LinhaResultado, PedidoConsulta, ResultadoConsulta
from motor.carga import BaseAnalitica
from motor.estatistica import destaque, fmt_int, fmt_lift, fmt_num, fmt_pct, montar_linhas

DESCRICAO_FERRAMENTAS = {
    "funil": "Abandono em cada etapa do funil (quem chegou na etapa anterior e não chegou nesta) e taxa de erro, por grupo.",
    "atrito": "FullStory: % de usuários que viram a página e tiveram dead click (tocar em algo que não responde) "
              "ou rage click (tocar várias vezes, irritado), por grupo, com o elemento mais afetado.",
    "tempo_na_pagina": "FullStory: mediana e p75 do tempo na página e % de visitas lentas (acima do p75 geral), por grupo.",
    "sequencia": "Usuários que fazem evento_a e depois evento_b em até janela_min minutos (ex.: bloqueia e desbloqueia rápido).",
    "temas_nps": "NPS: contagem de temas dos comentários por grupo (nunca o texto).",
}
DISPONIVEIS = ("funil", "atrito")  # as demais entram na Fase 2


class ErroConsulta(ValueError):
    """Pedido inválido: a mensagem é devolvida ao agente explicando o que é válido."""


def validar(base: BaseAnalitica, p: PedidoConsulta) -> None:
    if p.ferramenta not in DISPONIVEIS:
        raise ErroConsulta(f"A consulta '{p.ferramenta}' não está disponível. Use: {', '.join(DISPONIVEIS)}.")
    if p.segmentar_por != "nenhum" and p.segmentar_por not in base.dimensoes:
        raise ErroConsulta(f"Dimensão '{p.segmentar_por}' indisponível nestes dados. Use: nenhum, {', '.join(base.dimensoes)}.")
    if len(p.filtros) > MAX_FILTROS:
        raise ErroConsulta(f"No máximo {MAX_FILTROS} filtro por consulta (até 2 dimensões por tabela, por privacidade).")
    for f in p.filtros:
        if f.dimensao not in base.dimensoes:
            raise ErroConsulta(f"Filtro por '{f.dimensao}' indisponível nestes dados. Use: {', '.join(base.dimensoes)}.")
        if f.dimensao == p.segmentar_por:
            raise ErroConsulta("O filtro não pode usar a mesma dimensão da segmentação.")
        if f.valor not in base.valores[f.dimensao]:
            validos = ", ".join(base.valores[f.dimensao][:20])
            raise ErroConsulta(f"Valor '{f.valor}' não existe em {f.dimensao}. Valores válidos: {validos}.")
    if p.ferramenta == "atrito":
        if not base.tem_fullstory:
            raise ErroConsulta("Sem fullstory.csv: não há dados de atrito nesta análise.")
        if p.pagina not in base.paginas:
            raise ErroConsulta(f"Página '{p.pagina}' não pertence à jornada. Use: {', '.join(base.paginas)}.")
        if p.tipo_atrito not in ("dead_click", "rage_click"):
            raise ErroConsulta("Informe tipo_atrito: dead_click ou rage_click.")


def _recorte(metade: str, filtros) -> tuple[str, list]:
    condicoes, params = [], []
    if metade in ("A", "B"):
        condicoes.append("u.metade = ?")
        params.append(metade)
    for f in filtros:
        condicoes.append(f"CAST(u.{f.dimensao} AS VARCHAR) = ?")
        params.append(f.valor)
    return (" AND ".join(condicoes) or "TRUE"), params


def _grupo(segmentar_por: str) -> str:
    return "'todos'" if segmentar_por == "nenhum" else f"coalesce(CAST(u.{segmentar_por} AS VARCHAR), 'não informado')"


def _passos_de_abandono(base: BaseAnalitica) -> list[int]:
    """Etapas com métrica de abandono. Com evento de erro, a última etapa fica de fora:
    depois da confirmação só há sucesso ou erro, e "não chegou ao sucesso" repetiria o erro."""
    n = len(base.eventos_funil)
    return list(range(1, n - 1 if base.evento_erro else n))


def _funil(base: BaseAnalitica, p: PedidoConsulta, metade: str):
    etapas = base.eventos_funil
    recorte, params = _recorte(metade, p.filtros)
    passos = _passos_de_abandono(base)
    somas = []
    for i in passos:
        somas += [f"sum(f{i - 1}) AS n{i}", f"sum(f{i - 1} * (1 - f{i})) AS c{i}"]
    if base.evento_erro:
        k = len(etapas) - 2  # etapa imediatamente antes do sucesso (ex.: lock_confirm)
        somas += [f"sum(f{k}) AS n_erro", f"sum(f{k} * erro) AS c_erro"]
    sql = (f"SELECT {_grupo(p.segmentar_por)} AS grupo, {', '.join(somas)} "
           f"FROM funil_usuario fu JOIN usuarios u USING (user_id_hash) WHERE {recorte} GROUP BY 1 ORDER BY 1")
    df = base.cursor().execute(sql, params).fetchdf()
    metricas = [(f"abandono:{etapas[i]}", f"n{i}", f"c{i}", f"usuários que chegaram em {etapas[i - 1]}")
                for i in passos]
    if base.evento_erro:
        metricas.append((f"erro:{base.evento_erro}", "n_erro", "c_erro", f"usuários que chegaram em {etapas[-2]}"))
    linhas, geral = [], {}
    for metrica, cn, cc, descricao in metricas:
        grupos = [(str(r.grupo), int(getattr(r, cn)), int(getattr(r, cc))) for r in df.itertuples()]
        grupos = [g for g in grupos if g[1] > 0]
        total_n, total_c = sum(g[1] for g in grupos), sum(g[2] for g in grupos)
        linhas += montar_linhas(metrica, grupos, total_n, total_c)
        geral[f"base:{metrica}"] = descricao
        geral[f"n:{metrica}"] = total_n
        geral[f"taxa:{metrica}"] = total_c / total_n if total_n else 0.0
    return linhas, geral, "tagueamento", 1.0, sql


def _atrito(base: BaseAnalitica, p: PedidoConsulta, metade: str):
    recorte, params = _recorte(metade, p.filtros)
    sql = (
        "WITH v AS (SELECT user_id_hash, max(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END) AS viu, "
        "max(CASE WHEN event_type = ? THEN 1 ELSE 0 END) AS teve FROM fs WHERE page = ? GROUP BY 1) "
        f"SELECT {_grupo(p.segmentar_por)} AS grupo, sum(v.viu) AS n, sum(v.viu * v.teve) AS casos "
        f"FROM v JOIN usuarios u USING (user_id_hash) WHERE {recorte} GROUP BY 1 ORDER BY 1"
    )
    df = base.cursor().execute(sql, [p.tipo_atrito, p.pagina] + params).fetchdf()
    grupos = [(str(r.grupo), int(r.n), int(r.casos)) for r in df.itertuples() if r.n > 0]
    total_n, total_c = sum(g[1] for g in grupos), sum(g[2] for g in grupos)
    linhas = montar_linhas(p.tipo_atrito, grupos, total_n, total_c, FATOR_FULLSTORY)

    sql_el = (f"SELECT {_grupo(p.segmentar_por)} AS grupo, fs.element AS elemento, count(*) AS qtd "
              f"FROM fs JOIN usuarios u USING (user_id_hash) WHERE fs.page = ? AND fs.event_type = ? AND {recorte} "
              "GROUP BY 1, 2")
    el = base.cursor().execute(sql_el, [p.pagina, p.tipo_atrito] + params).fetchdf()
    for linha in linhas:
        do_grupo = el[el.grupo == linha.grupo]
        if len(do_grupo) and do_grupo.qtd.sum() > 0:
            topo = do_grupo.sort_values(["qtd", "elemento"], ascending=[False, True]).iloc[0]
            linha.extras = {"elemento": str(topo.elemento), "elemento_pct": float(topo.qtd / do_grupo.qtd.sum())}
    geral = {f"base:{p.tipo_atrito}": f"usuários com visita a {p.pagina} na amostra do FullStory",
             f"n:{p.tipo_atrito}": total_n, f"taxa:{p.tipo_atrito}": total_c / total_n if total_n else 0.0}
    return linhas, geral, "fullstory", FATOR_FULLSTORY, sql


_EXECUTORES: dict[str, Callable] = {"funil": _funil, "atrito": _atrito}


def executar(base: BaseAnalitica, p: PedidoConsulta, metade: str, query_id: str) -> ResultadoConsulta:
    inicio = time.perf_counter()
    linhas, geral, fonte, fator, sql = _EXECUTORES[p.ferramenta](base, p, metade)
    avisos = []
    if fonte == "fullstory":
        avisos.append("FullStory é amostra de 10% das sessões: contagens absolutas são estimativas (×10).")
    return ResultadoConsulta(
        query_id=query_id, ferramenta=p.ferramenta, params=p.model_dump(exclude={"motivo"}), metade=metade,
        fonte=fonte, fator_extrapolacao=fator, linhas=linhas, geral=geral, avisos=avisos, sql=sql,
        duracao_ms=round((time.perf_counter() - inicio) * 1000, 1),
    )


def descrever_params(params: dict) -> str:
    partes = []
    for chave in ("pagina", "tipo_atrito", "evento_a", "evento_b", "janela_min"):
        if params.get(chave) is not None:
            partes.append(f"{chave}={params[chave]}")
    seg = params.get("segmentar_por", "nenhum")
    partes.append("sem segmentação" if seg == "nenhum" else f"por {seg}")
    for f in params.get("filtros", []):
        partes.append(f"filtro {f['dimensao']}={f['valor']}")
    return " · ".join(partes)


def texto_para_agente(res: ResultadoConsulta, restantes: int | None = None) -> str:
    """Tabela compacta que o agente lê. Grupos suprimidos aparecem só pelo nome, sem números."""
    cabecalho = f"[{res.query_id}] {res.ferramenta} · {descrever_params(res.params)} · metade {res.metade}"
    if restantes is not None:
        cabecalho += f" · consultas restantes: {restantes}"
    saida = [cabecalho]
    por_metrica: OrderedDict[str, list[LinhaResultado]] = OrderedDict()
    for linha in res.linhas:
        por_metrica.setdefault(linha.metrica, []).append(linha)
    extrapola = res.fator_extrapolacao != 1.0
    for metrica, linhas in por_metrica.items():
        n_base = res.geral.get(f"n:{metrica}")
        taxa_geral = res.geral.get(f"taxa:{metrica}")
        saida.append(f"métrica {metrica} · base: {res.geral.get(f'base:{metrica}', '')} "
                     f"(n={fmt_int(n_base or 0)}, taxa geral {fmt_pct(taxa_geral)})")
        visiveis = sorted((l for l in linhas if not l.suprimido), key=lambda l: -(l.lift or 0))[:12]
        colunas = ["grupo", "n", "casos", "taxa", "demais", "dif. p.p.", "lift (IC95)", "% dos casos"]
        if extrapola:
            colunas.append("casos ≈×10")
        if res.ferramenta == "atrito":
            colunas.append("elemento")
        saida.append(" | ".join(colunas))
        for l in visiveis:
            marca = " ▲" if destaque(l) else (" (n<300)" if min(l.n, l.n_resto or l.n) < N_MIN_EVIDENCIA else "")
            dif = "—" if l.taxa_resto is None else fmt_num(100 * (l.taxa - l.taxa_resto), 1)
            celulas = [l.grupo, fmt_int(l.n), fmt_int(l.casos), fmt_pct(l.taxa), fmt_pct(l.taxa_resto), dif,
                       fmt_lift(l) + marca, fmt_pct(l.participacao)]
            if extrapola:
                celulas.append("≈ " + fmt_int(l.casos_extrapolados or 0))
            if res.ferramenta == "atrito":
                el = l.extras.get("elemento")
                celulas.append(f"{el} ({fmt_pct(l.extras.get('elemento_pct'), 0)})" if el else "—")
            saida.append(" | ".join(celulas))
        suprimidos = [l.grupo for l in linhas if l.suprimido]
        if suprimidos:
            saida.append("suprimidos (n<50, sem números): " + ", ".join(suprimidos))
    saida += [f"aviso: {a}" for a in res.avisos]
    return "\n".join(saida)
