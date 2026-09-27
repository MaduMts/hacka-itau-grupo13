"""Panorama inicial (`Q00-A`): o que o agente recebe antes de planejar as consultas.

Funil geral e dimensões disponíveis com contagens. Fica registrado como uma consulta,
então qualquer número dele que apareça no dossiê tem fonte.
"""
from __future__ import annotations

from config import N_MIN_EXIBICAO
from contratos import ResultadoConsulta
from motor.carga import BaseAnalitica
from motor.estatistica import fmt_int, fmt_pct, montar_linhas


def panorama(base: BaseAnalitica, metade: str = "A", query_id: str = "Q00-A") -> ResultadoConsulta:
    cur = base.cursor()
    filtro = "" if metade == "T" else "WHERE u.metade = ?"
    params = [] if metade == "T" else [metade]
    etapas = base.eventos_funil
    somas = ", ".join(f"sum(f{i}) AS e{i}" for i in range(len(etapas)))
    if base.evento_erro:
        somas += ", sum(erro) AS erro"
    linha = cur.execute(
        f"SELECT count(*) AS usuarios, {somas} FROM funil_usuario fu JOIN usuarios u USING (user_id_hash) {filtro}", params
    ).fetchone()
    n_usuarios, atingiram = int(linha[0]), [int(x or 0) for x in linha[1:1 + len(etapas)]]
    n_erro = int(linha[-1] or 0) if base.evento_erro else 0

    linhas = []
    for i in range(1, len(etapas) - 1 if base.evento_erro else len(etapas)):
        linhas += montar_linhas(f"abandono:{etapas[i]}", [("todos", atingiram[i - 1], atingiram[i - 1] - atingiram[i])],
                                atingiram[i - 1], atingiram[i - 1] - atingiram[i])
    if base.evento_erro:
        linhas += montar_linhas(f"erro:{base.evento_erro}", [("todos", atingiram[-2], n_erro)], atingiram[-2], n_erro)

    texto = [f"[{query_id}] panorama · metade {metade} · período {base.inicio:%d/%m/%Y} a {base.fim:%d/%m/%Y}",
             f"usuários: {fmt_int(n_usuarios)} · eventos de tagueamento no período (base toda): {fmt_int(base.n_eventos)}",
             "funil geral (usuários que atingiram cada etapa):"]
    geral: dict[str, float | int | str] = {"usuarios": n_usuarios, "eventos": base.n_eventos}
    for i, ev in enumerate(etapas):
        conv = ""
        if i and atingiram[i - 1]:
            geral[f"conv:{ev}"] = atingiram[i] / atingiram[i - 1]
            conv = f" · conversão da etapa anterior {fmt_pct(geral[f'conv:{ev}'])}"
        geral[f"atingiram:{ev}"] = atingiram[i]
        texto.append(f"- {ev}: {fmt_int(atingiram[i])}{conv}")
    if base.evento_erro:
        texto.append(f"- {base.evento_erro}: {fmt_int(n_erro)} "
                     f"({fmt_pct(n_erro / atingiram[-2] if atingiram[-2] else None)} de quem chegou em {etapas[-2]})")

    texto.append("dimensões disponíveis (grupos com n<50 aparecem como <50):")
    for d in base.dimensoes:
        contagens = cur.execute(
            f"SELECT CAST({d} AS VARCHAR) AS valor, count(*) AS n FROM usuarios u {filtro} GROUP BY 1 ORDER BY 2 DESC", params
        ).fetchall()
        partes = [f"{v} ({fmt_int(n) if n >= N_MIN_EXIBICAO else '<50'})" for v, n in contagens]
        texto.append(f"- {d}: " + ", ".join(partes))
    if not base.tem_perfil:
        texto.append("- sem perfil.csv: não há faixa etária, segmento nem tempo de conta. Não infira essas dimensões.")

    if base.tem_fullstory:
        n_fs = cur.execute(
            f"SELECT count(DISTINCT fs.user_id_hash) FROM fs JOIN usuarios u USING (user_id_hash) {filtro}", params
        ).fetchone()[0]
        geral["usuarios_fullstory"] = int(n_fs)
        texto.append(f"FullStory (amostra de 10% das sessões): {fmt_int(n_fs)} usuários com sessão gravada; "
                     f"páginas com dados: {', '.join(base.paginas_com_dados)}")
    else:
        texto.append("sem fullstory.csv: não há medidas de atrito (dead click, rage click, tempo na tela).")
    if base.tem_nps:
        n_nps = cur.execute(f"SELECT count(*) FROM nps JOIN usuarios u USING (user_id_hash) {filtro}", params).fetchone()[0]
        geral["respondentes_nps"] = int(n_nps)
        texto.append(f"NPS: {fmt_int(n_nps)} respondentes (autosselecionados); comentários chegam só como contagens de temas.")
    else:
        texto.append("sem nps.csv: não há pistas do porquê vindas da voz do cliente.")

    return ResultadoConsulta(query_id=query_id, ferramenta="panorama", params={"metade": metade}, metade=metade,
                             fonte="misto", linhas=linhas, geral=geral, texto_llm="\n".join(texto))
