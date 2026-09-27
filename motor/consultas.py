"""Catálogo de consultas parametrizadas: o agente escolhe, o código calcula.

Cada consulta devolve linhas agregadas (grupo × métrica) com lift, IC e supressão de grupos
pequenos, mais um texto compacto para o agente (`texto_para_agente`). Não existe SQL livre:
dimensões vêm de uma lista fechada e valores de filtro entram como parâmetros. Toda consulta
segmentada traz também a linha da população ("todos"), sem lift.
"""
from __future__ import annotations

import hashlib
import time
import unicodedata
from collections import OrderedDict
from typing import Any, Callable

from config import FATOR_FULLSTORY, MAX_FILTROS, N_MIN_EVIDENCIA
from contratos import LinhaResultado, PedidoConsulta, ResultadoConsulta
from motor.carga import BaseAnalitica
from motor.estatistica import destaque, fmt_int, fmt_lift, fmt_num, fmt_pct, montar_linhas

DESCRICAO_FERRAMENTAS = {
    "funil": "Abandono em cada etapa do funil (quem chegou na etapa anterior e não chegou nesta) e taxa de erro, por grupo.",
    "atrito": "FullStory: % de usuários que viram a página e tiveram dead click (tocar em algo que não responde) "
              "ou rage click (tocar várias vezes, irritado), por grupo, com o elemento mais afetado.",
    "tempo_na_pagina": "FullStory: % de usuários lentos na página (acima do p75 geral de tempo na página), "
                       "com mediana e p75 do tempo, por grupo.",
    "sequencia": "Usuários que fazem evento_a e depois evento_b em até janela_min minutos (ex.: bloqueia e desbloqueia "
                 "rápido), com a distribuição do tempo entre os dois. Mostra comportamento, não intenção.",
    "temas_nps": "NPS: % de respondentes cujo comentário cita cada tema, e % de detratores, por grupo. "
                 "Os comentários nunca são enviados, só as contagens.",
}
FAIXAS_SEQUENCIA = [(60, "<1 min"), (300, "1–5 min"), (600, "5–10 min"), (3600, "10–60 min"), (86400, "1–24 h"),
                    (None, ">1 dia")]


class ErroConsulta(ValueError):
    """Pedido inválido: a mensagem é devolvida ao agente explicando o que é válido."""


def disponiveis(base: BaseAnalitica) -> list[str]:
    ferramentas = ["funil"]
    if base.tem_fullstory:
        ferramentas += ["atrito", "tempo_na_pagina"]
    ferramentas.append("sequencia")
    if base.tem_nps:
        ferramentas.append("temas_nps")
    return ferramentas


def validar(base: BaseAnalitica, p: PedidoConsulta) -> None:
    if p.ferramenta not in disponiveis(base):
        raise ErroConsulta(f"A consulta '{p.ferramenta}' não está disponível nestes dados. Use: {', '.join(disponiveis(base))}.")
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
    if p.ferramenta in ("atrito", "tempo_na_pagina") and p.pagina not in base.paginas:
        raise ErroConsulta(f"Página '{p.pagina}' não pertence à jornada. Use: {', '.join(base.paginas)}.")
    if p.ferramenta == "atrito" and p.tipo_atrito not in ("dead_click", "rage_click"):
        raise ErroConsulta("Informe tipo_atrito: dead_click ou rage_click.")
    if p.ferramenta == "sequencia":
        eventos = base.eventos_jornada
        if p.evento_a not in eventos or p.evento_b not in eventos or p.evento_a == p.evento_b:
            raise ErroConsulta(f"Informe evento_a e evento_b diferentes, entre: {', '.join(eventos)}.")
        if p.janela_min is None or not 1 <= p.janela_min <= 1440:
            raise ErroConsulta("Informe janela_min entre 1 e 1440 minutos.")


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


def _linhas_por_metrica(metrica: str, df, coluna_n: str, coluna_c: str,
                        fator: float = 1.0) -> tuple[list[LinhaResultado], int, int]:
    grupos = [(str(r.grupo), int(getattr(r, coluna_n) or 0), int(getattr(r, coluna_c) or 0)) for r in df.itertuples()]
    grupos = [g for g in grupos if g[1] > 0]
    total_n, total_c = sum(g[1] for g in grupos), sum(g[2] for g in grupos)
    return montar_linhas(metrica, grupos, total_n, total_c, fator), total_n, total_c


def _geral(geral: dict, metrica: str, descricao: str, total_n: int, total_c: int) -> None:
    geral[f"base:{metrica}"] = descricao
    geral[f"n:{metrica}"] = total_n
    geral[f"taxa:{metrica}"] = total_c / total_n if total_n else 0.0


def _passos_de_abandono(base: BaseAnalitica) -> list[int]:
    """Etapas com métrica de abandono. Com evento de erro, a última etapa fica de fora:
    depois da confirmação só há sucesso ou erro, e "não chegou ao sucesso" repetiria o erro."""
    n = len(base.eventos_funil)
    return list(range(1, n - 1 if base.evento_erro else n))


def _funil(base: BaseAnalitica, p: PedidoConsulta, metade: str, **_: Any):
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
    metricas = [(f"abandono:{etapas[i]}", f"n{i}", f"c{i}", f"usuários que chegaram em {etapas[i - 1]}") for i in passos]
    if base.evento_erro:
        metricas.append((f"erro:{base.evento_erro}", "n_erro", "c_erro", f"usuários que chegaram em {etapas[-2]}"))
    linhas, geral = [], {}
    for metrica, cn, cc, descricao in metricas:
        ls, tn, tc = _linhas_por_metrica(metrica, df, cn, cc)
        linhas += ls
        _geral(geral, metrica, descricao, tn, tc)
    return linhas, geral, "tagueamento", 1.0, sql


def _atrito(base: BaseAnalitica, p: PedidoConsulta, metade: str, **_: Any):
    recorte, params = _recorte(metade, p.filtros)
    sql = (
        "WITH v AS (SELECT user_id_hash, max(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END) AS viu, "
        "max(CASE WHEN event_type = ? THEN 1 ELSE 0 END) AS teve FROM fs WHERE page = ? GROUP BY 1) "
        f"SELECT {_grupo(p.segmentar_por)} AS grupo, sum(v.viu) AS n, sum(v.viu * v.teve) AS casos "
        f"FROM v JOIN usuarios u USING (user_id_hash) WHERE {recorte} GROUP BY 1 ORDER BY 1"
    )
    df = base.cursor().execute(sql, [p.tipo_atrito, p.pagina] + params).fetchdf()
    linhas, tn, tc = _linhas_por_metrica(p.tipo_atrito, df, "n", "casos", FATOR_FULLSTORY)
    sql_el = (f"SELECT {_grupo(p.segmentar_por)} AS grupo, fs.element AS elemento, count(*) AS qtd "
              f"FROM fs JOIN usuarios u USING (user_id_hash) WHERE fs.page = ? AND fs.event_type = ? AND {recorte} "
              "GROUP BY 1, 2")
    el = base.cursor().execute(sql_el, [p.pagina, p.tipo_atrito] + params).fetchdf()
    for linha in linhas:
        do_grupo = el[el.grupo == linha.grupo]
        if len(do_grupo) and do_grupo.qtd.sum() > 0:
            topo = do_grupo.sort_values(["qtd", "elemento"], ascending=[False, True]).iloc[0]
            linha.extras = {"elemento": str(topo.elemento), "elemento_pct": float(topo.qtd / do_grupo.qtd.sum())}
    geral: dict = {}
    _geral(geral, p.tipo_atrito, f"usuários com visita a {p.pagina} na amostra do FullStory", tn, tc)
    return linhas, geral, "fullstory", FATOR_FULLSTORY, sql


def _tempo_na_pagina(base: BaseAnalitica, p: PedidoConsulta, metade: str, limiar_s: float | None = None, **_: Any):
    if limiar_s is None:  # p75 geral da página na metade A; fica congelado para as réplicas
        limiar_s = base.cursor().execute(
            "SELECT quantile_cont(time_on_page_s, 0.75) FROM fs JOIN usuarios u USING (user_id_hash) "
            "WHERE fs.page = ? AND fs.event_type = 'page_view' AND u.metade = 'A'", [p.pagina]
        ).fetchone()[0] or 0.0
    limiar_s = round(float(limiar_s), 1)
    recorte, params = _recorte(metade, p.filtros)
    sql = (
        "WITH pu AS (SELECT user_id_hash, max(time_on_page_s) AS t_max, median(time_on_page_s) AS t_med FROM fs "
        "WHERE page = ? AND event_type = 'page_view' AND time_on_page_s IS NOT NULL GROUP BY 1) "
        f"SELECT {_grupo(p.segmentar_por)} AS grupo, count(*) AS n, count(*) FILTER (WHERE pu.t_max > ?) AS casos, "
        "median(pu.t_med) AS mediana_s, quantile_cont(pu.t_med, 0.75) AS p75_s "
        f"FROM pu JOIN usuarios u USING (user_id_hash) WHERE {recorte} GROUP BY 1 ORDER BY 1"
    )
    df = base.cursor().execute(sql, [p.pagina, limiar_s] + params).fetchdf()
    linhas, tn, tc = _linhas_por_metrica("lento", df, "n", "casos", FATOR_FULLSTORY)
    tempos = {str(r.grupo): (r.mediana_s, r.p75_s) for r in df.itertuples()}
    for linha in linhas:
        mediana, p75 = tempos.get(linha.grupo, (None, None))
        if mediana is not None:
            linha.extras = {"mediana_s": round(float(mediana), 1), "p75_s": round(float(p75), 1)}
    geral: dict = {"limiar_s": limiar_s}
    _geral(geral, "lento", f"usuários com visita a {p.pagina} na amostra do FullStory "
                           f"(lento = acima de {fmt_num(limiar_s, 1)} s)", tn, tc)
    return linhas, geral, "fullstory", FATOR_FULLSTORY, sql


def _tabela_sequencia(base: BaseAnalitica, evento_a: str, evento_b: str) -> str:
    """Tempo mínimo de cada usuário entre evento_a e o evento_b seguinte, calculado uma vez por par
    (ASOF join sobre o tagueamento) e reaproveitado por grupos, metades e réplicas."""
    nome = "seq_" + hashlib.md5(f"{evento_a}|{evento_b}".encode()).hexdigest()[:10]
    base.cursor().execute(
        f"CREATE TABLE IF NOT EXISTS {nome} AS "
        "WITH a AS (SELECT user_id_hash, ts FROM tag WHERE event_name = ?), "
        "b AS (SELECT user_id_hash, ts AS ts_b FROM tag WHERE event_name = ?), "
        "par AS (SELECT a.user_id_hash, date_diff('second', a.ts, b.ts_b) AS delta_s "
        "FROM a ASOF LEFT JOIN b ON a.user_id_hash = b.user_id_hash AND a.ts <= b.ts_b) "
        "SELECT user_id_hash, min(delta_s) AS delta_s FROM par GROUP BY 1",
        [evento_a, evento_b],
    )
    return nome


def _sequencia(base: BaseAnalitica, p: PedidoConsulta, metade: str, **_: Any):
    recorte, params = _recorte(metade, p.filtros)
    janela_s = int(p.janela_min) * 60
    tabela = _tabela_sequencia(base, p.evento_a, p.evento_b)
    sql = (f"SELECT {_grupo(p.segmentar_por)} AS grupo, count(*) AS n, "
           f"count(*) FILTER (WHERE pu.delta_s <= {janela_s}) AS casos, "
           f"median(pu.delta_s) FILTER (WHERE pu.delta_s <= {janela_s}) AS mediana_s "
           f"FROM {tabela} pu JOIN usuarios u USING (user_id_hash) WHERE {recorte} GROUP BY 1 ORDER BY 1")
    df = base.cursor().execute(sql, params).fetchdf()
    linhas, tn, tc = _linhas_por_metrica("sequencia", df, "n", "casos")
    medianas = {str(r.grupo): r.mediana_s for r in df.itertuples()}
    for linha in linhas:
        mediana = medianas.get(linha.grupo)
        if mediana is not None and mediana == mediana:  # não é NaN
            linha.extras = {"mediana_min": round(float(mediana) / 60, 1)}

    casos = " ".join(f"WHEN pu.delta_s < {limite} THEN '{rotulo}'" for limite, rotulo in FAIXAS_SEQUENCIA if limite)
    faixas = base.cursor().execute(
        f"SELECT CASE WHEN pu.delta_s IS NULL THEN 'sem {p.evento_b}' {casos} ELSE '>1 dia' END AS faixa, "
        f"count(*) AS qtd FROM {tabela} pu JOIN usuarios u USING (user_id_hash) WHERE {recorte} GROUP BY 1",
        params,
    ).fetchall()
    geral: dict = {f"faixa:{faixa}": int(qtd) for faixa, qtd in sorted(faixas, key=lambda x: _ordem_faixa(x[0]))}
    _geral(geral, "sequencia", f"usuários com {p.evento_a}; casos = {p.evento_b} em até {p.janela_min} min", tn, tc)
    return linhas, geral, "tagueamento", 1.0, sql


def _ordem_faixa(rotulo: str) -> int:
    rotulos = [r for _, r in FAIXAS_SEQUENCIA]
    return rotulos.index(rotulo) if rotulo in rotulos else len(rotulos)


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").casefold()


def _temas_nps(base: BaseAnalitica, p: PedidoConsulta, metade: str, **_: Any):
    recorte, params = _recorte(metade, p.filtros)
    temas = {nome: "|".join(_normalizar(t).replace("|", " ") for t in termos) for nome, termos in base.temas_nps.items()}
    somas = [f"sum(CASE WHEN regexp_matches(lower(strip_accents(n.comentario)), ?) THEN 1 ELSE 0 END) AS c{i}"
             for i in range(len(temas))]
    somas.append("sum(CASE WHEN n.score <= 6 THEN 1 ELSE 0 END) AS c_det")
    sql = (f"SELECT {_grupo(p.segmentar_por)} AS grupo, count(*) AS n, {', '.join(somas)} "
           f"FROM nps n JOIN usuarios u USING (user_id_hash) WHERE {recorte} GROUP BY 1 ORDER BY 1")
    df = base.cursor().execute(sql, list(temas.values()) + params).fetchdf()
    linhas, geral = [], {}
    for i, nome in enumerate(temas):
        ls, tn, tc = _linhas_por_metrica(f"tema:{nome}", df, "n", f"c{i}")
        linhas += ls
        _geral(geral, f"tema:{nome}", "respondentes do NPS (autosselecionados)", tn, tc)
    ls, tn, tc = _linhas_por_metrica("detrator", df, "n", "c_det")
    linhas += ls
    _geral(geral, "detrator", "respondentes do NPS (autosselecionados); detrator = nota de 0 a 6", tn, tc)
    return linhas, geral, "nps", 1.0, sql


_EXECUTORES: dict[str, Callable] = {"funil": _funil, "atrito": _atrito, "tempo_na_pagina": _tempo_na_pagina,
                                    "sequencia": _sequencia, "temas_nps": _temas_nps}


def executar(base: BaseAnalitica, p: PedidoConsulta, metade: str, query_id: str,
             limiar_s: float | None = None) -> ResultadoConsulta:
    inicio = time.perf_counter()
    executor = _EXECUTORES[p.ferramenta]
    linhas, geral, fonte, fator, sql = executor(base, p, metade, limiar_s=limiar_s)
    if p.segmentar_por != "nenhum":  # linha da população, para candidatas sem grupo
        congelado = geral.get("limiar_s", limiar_s)
        populacao, _, _, _, _ = executor(base, p.model_copy(update={"segmentar_por": "nenhum"}), metade, limiar_s=congelado)
        linhas += populacao
    params = p.model_dump(exclude={"motivo"})
    if "limiar_s" in geral:
        params["limiar_s"] = geral["limiar_s"]
    avisos = []
    if fonte == "fullstory":
        avisos.append("FullStory é amostra de 10% das sessões: contagens absolutas são estimativas (×10).")
    if fonte == "nps":
        avisos.append("NPS: respondentes se autosselecionam; serve como pista do porquê, não como evidência.")
    return ResultadoConsulta(
        query_id=query_id, ferramenta=p.ferramenta, params=params, metade=metade, fonte=fonte,
        fator_extrapolacao=fator, linhas=linhas, geral=geral, avisos=avisos, sql=sql,
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


def _colunas_extras(ferramenta: str) -> list[tuple[str, Callable[[LinhaResultado], str]]]:
    if ferramenta == "atrito":
        return [("elemento", lambda l: f"{l.extras['elemento']} ({fmt_pct(l.extras['elemento_pct'], 0)})"
                 if l.extras.get("elemento") else "—")]
    if ferramenta == "tempo_na_pagina":
        return [("mediana", lambda l: f"{fmt_num(l.extras['mediana_s'], 1)} s" if "mediana_s" in l.extras else "—"),
                ("p75", lambda l: f"{fmt_num(l.extras['p75_s'], 1)} s" if "p75_s" in l.extras else "—")]
    if ferramenta == "sequencia":
        return [("mediana entre eventos", lambda l: f"{fmt_num(l.extras['mediana_min'], 1)} min"
                 if "mediana_min" in l.extras else "—")]
    return []


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
    extras = _colunas_extras(res.ferramenta)
    segmentada = res.params.get("segmentar_por", "nenhum") != "nenhum"
    for metrica, linhas in por_metrica.items():
        saida.append(f"métrica {metrica} · base: {res.geral.get(f'base:{metrica}', '')} "
                     f"(n={fmt_int(res.geral.get(f'n:{metrica}') or 0)}, taxa geral {fmt_pct(res.geral.get(f'taxa:{metrica}'))})")
        visiveis = [l for l in linhas if not l.suprimido and not (segmentada and l.grupo == "todos")]
        visiveis = sorted(visiveis, key=lambda l: -(l.lift or 0))[:12]
        colunas = ["grupo", "n", "casos", "taxa", "demais", "dif. p.p.", "lift (IC95)", "% dos casos"]
        colunas += (["casos ≈×10"] if extrapola else []) + [nome for nome, _ in extras]
        saida.append(" | ".join(colunas))
        for l in visiveis:
            pequeno = segmentada and min(l.n, l.n_resto or l.n) < N_MIN_EVIDENCIA
            marca = " ▲" if destaque(l) else (" (n<300)" if pequeno else "")
            dif = "—" if l.taxa_resto is None else fmt_num(100 * (l.taxa - l.taxa_resto), 1)
            celulas = [l.grupo, fmt_int(l.n), fmt_int(l.casos), fmt_pct(l.taxa), fmt_pct(l.taxa_resto), dif,
                       fmt_lift(l) + marca, fmt_pct(l.participacao)]
            if extrapola:
                celulas.append("≈ " + fmt_int(l.casos_extrapolados or 0))
            celulas += [funcao(l) for _, funcao in extras]
            saida.append(" | ".join(celulas))
        suprimidos = [l.grupo for l in linhas if l.suprimido]
        if suprimidos:
            saida.append("suprimidos (n<50, sem números): " + ", ".join(suprimidos))
    faixas = [(k.split(":", 1)[1], v) for k, v in res.geral.items() if k.startswith("faixa:")]
    if faixas:
        saida.append("distribuição do tempo entre os eventos (usuários): " + ", ".join(f"{r}: {fmt_int(v)}" for r, v in faixas))
    if "limiar_s" in res.geral:
        saida.append(f"lento = mais de {fmt_num(float(res.geral['limiar_s']), 1)} s na página (p75 geral da metade A)")
    saida += [f"aviso: {a}" for a in res.avisos]
    return "\n".join(saida)
