"""Rótulos por regras fixas no código (o LLM não escolhe o rótulo), deduplicação e ranking.

Ordem das regras (a primeira que casar vence):
  R2 Hipótese   explicação causal, comportamento sem contraste (sequência) ou candidata sem grupo
  R3 Descartada sem lift_A ≥ 1,5 com IC95 acima de 1 (ex.: armadilha de taxa-base)
  R4 Indício    n ou casos pequenos em alguma metade (ex.: armadilha de amostra pequena)
  R5 Indício    não replicou na metade B (mesma direção, lift_B ≥ 1,25 e IC99 acima de 1)
  R6 Indício    veio do NPS (respondentes autosselecionados)
  R7 Evidência  passou em tudo; vira Indício se algum número ficou sem fonte
Lacunas (R1) vêm da checagem de entrada e do agente, fora das candidatas.
"""
from __future__ import annotations

from config import CASOS_MIN, LIFT_MIN_A, LIFT_MIN_B, N_MIN_EVIDENCIA
from contratos import Candidata, Hipotese, LinhaResultado, Rotulo, Verificacao
from motor.estatistica import fmt_int, fmt_num
from verificacao.numeros import CAUSAL
from verificacao.refutacao import Replica

ORDEM = {Rotulo.EVIDENCIA: 0, Rotulo.HIPOTESE: 1, Rotulo.INDICIO: 2, Rotulo.DESCARTADA: 3, Rotulo.LACUNA: 4}
STATUS = {Rotulo.EVIDENCIA: "principal", Rotulo.HIPOTESE: "research", Rotulo.INDICIO: "indicio",
          Rotulo.DESCARTADA: "descartada", Rotulo.LACUNA: "lacuna"}


def _ic(ic: tuple[float, float] | None) -> str:
    return "—" if ic is None else f"{fmt_num(ic[0])}–{fmt_num(ic[1])}"


def _suficiente(l: LinhaResultado) -> bool:
    return min(l.n, l.n_resto) >= N_MIN_EVIDENCIA and l.casos >= CASOS_MIN


def rotular(c: Candidata, rep: Replica, verificada: bool) -> tuple[Rotulo, str, str]:
    grupo = c.evidencia_principal.grupo
    if c.tipo == "explicacao_causal" or CAUSAL.search(c.enunciado):
        return Rotulo.HIPOTESE, "R2", "Explicação causal: depende de Research ou experimento."
    if rep.familia == "comportamental":
        return Rotulo.HIPOTESE, "R2", "Comportamento medido sem comparação entre grupos: o dado mostra quanto; o porquê fica com a Research."
    if grupo is None:
        return Rotulo.HIPOTESE, "R2", "Sem grupo de comparação: o dado mostra quanto; o porquê fica com a Research."
    la, lb = rep.linha_a, rep.linha_b
    if la is None or la.suprimido:
        return Rotulo.DESCARTADA, "R3", "Grupo inexistente ou com menos de 50 usuários: não pode ser citado."
    if not (la.lift is not None and la.ic95 is not None and la.lift >= LIFT_MIN_A and la.ic95[0] > 1):
        lift = "—" if la.lift is None else fmt_num(la.lift)
        return Rotulo.DESCARTADA, "R3", (f"Lift {lift} (IC95 {_ic(la.ic95)}) sem efeito claro: "
                                         f"grupo com {fmt_int(la.n)} usuários, mas volume alto não é insight.")
    if not _suficiente(la):
        return Rotulo.INDICIO, "R4", (f"Amostra pequena: {fmt_int(la.n)} usuários e {fmt_int(la.casos)} casos no grupo na metade A "
                                      f"(mínimo {N_MIN_EVIDENCIA} usuários e {CASOS_MIN} casos em cada metade).")
    replicou = (lb is not None and not lb.suprimido and _suficiente(lb) and lb.lift is not None
                and lb.ic99 is not None and lb.lift >= LIFT_MIN_B and lb.ic99[0] > 1)
    if not replicou:
        detalhe = "sem dados na metade B" if lb is None else f"lift B {fmt_num(lb.lift or 0)}, IC99 {_ic(lb.ic99)}"
        return Rotulo.INDICIO, "R5", f"Não replicou na metade B ({detalhe})."
    if rep.fonte == "nps":
        return Rotulo.INDICIO, "R6", "Vem do NPS (respondentes autosselecionados): serve como pista, não como evidência."
    texto = f"Replicou na metade B: lift A {fmt_num(la.lift)} (IC95 {_ic(la.ic95)}), lift B {fmt_num(lb.lift)} (IC99 {_ic(lb.ic99)})."
    if not verificada:
        return Rotulo.INDICIO, "R7", "Rebaixada: número sem fonte depois da correção. " + texto
    return Rotulo.EVIDENCIA, "R7", texto


def _afetados(rep: Replica) -> tuple[int | None, bool, float]:
    """Usuários afetados no total (estimado ×10 no FullStory) e impacto = afetados em excesso."""
    lt = rep.linha_t
    if lt is None:
        return None, False, 0.0
    fator = rep.fator_extrapolacao
    afetados = round(lt.casos * fator)
    base = lt.taxa_resto if lt.taxa_resto is not None else lt.taxa
    impacto = max(lt.casos - lt.n * base, 0.0) * fator if lt.taxa_resto is not None else lt.casos * fator
    return afetados, fator != 1.0, round(impacto, 1)


def montar_hipotese(c: Candidata, rep: Replica, verificacao: Verificacao) -> Hipotese:
    rotulo, regra, motivo = rotular(c, rep, verificacao.verificada)
    afetados, extrapolado, impacto = _afetados(rep)
    return Hipotese(
        id="", candidata=c, familia=rep.familia, fonte=rep.fonte, dimensao=rep.segmentar_por,  # type: ignore[arg-type]
        linha_a=rep.linha_a, linha_b=rep.linha_b, linha_t=rep.linha_t, query_ids=rep.query_ids,
        rotulo=rotulo, regra=regra, motivo=motivo, status=STATUS[rotulo],  # type: ignore[arg-type]
        verificacao=verificacao, usuarios_afetados=afetados, extrapolado=extrapolado, impacto=impacto,
    )


def _chave(h: Hipotese) -> tuple:
    if h.familia == "comportamental":
        return ("comportamento", h.candidata.evidencia_principal.metrica)
    return (h.dimensao or "nenhum", h.candidata.evidencia_principal.grupo)


def _ordem(h: Hipotese) -> tuple:
    return (ORDEM[h.rotulo], not h.verificacao.verificada, -h.impacto)


def deduplicar(hs: list[Hipotese]) -> list[Hipotese]:
    """Uma hipótese por (dimensão, grupo). As repetidas viram evidência complementar da melhor.

    Também junta plataforma com versão da mesma plataforma ("android" ⊃ "android 8.4.0"),
    ficando com a mais específica quando o lift dela é pelo menos igual.
    """
    melhores: dict[tuple, Hipotese] = {}
    for h in sorted(hs, key=_ordem):
        chave = _chave(h)
        if chave in melhores:
            _complementar(melhores[chave], h)
        else:
            melhores[chave] = h
    finais = list(melhores.values())
    for geral in [h for h in finais if h.dimensao == "plataforma"]:
        for especifica in finais:
            if (especifica is not geral and especifica.dimensao == "versao_app"
                    and (especifica.candidata.evidencia_principal.grupo or "").startswith(f"{geral.candidata.evidencia_principal.grupo} ")
                    and (especifica.linha_a and geral.linha_a and (especifica.linha_a.lift or 0) >= (geral.linha_a.lift or 0))):
                _complementar(especifica, geral)
                finais = [h for h in finais if h is not geral]
                break
    return finais


def _complementar(principal: Hipotese, outra: Hipotese) -> None:
    la = outra.linha_a
    lift = "—" if la is None or la.lift is None else fmt_num(la.lift)
    principal.evidencias_complementares.append(
        f"{outra.query_ids.get('A', '?')} · {outra.candidata.evidencia_principal.metrica} · lift {lift} ({outra.rotulo.value})"
    )


def ranquear(hs: list[Hipotese]) -> dict[str, list[Hipotese]]:
    """Top 2 (Evidência > Hipótese > Indício) viram as hipóteses do dossiê; o resto vai por rótulo."""
    ordenadas = sorted(hs, key=_ordem)
    elegiveis = [h for h in ordenadas if h.rotulo in (Rotulo.EVIDENCIA, Rotulo.HIPOTESE, Rotulo.INDICIO)]
    principais = elegiveis[:2]
    grupos: dict[str, list[Hipotese]] = {"principais": principais, "outras_evidencias": [], "research": [],
                                         "indicios": [], "descartadas": []}
    destino = {Rotulo.EVIDENCIA: ("outras_evidencias", "evidencia_extra"), Rotulo.HIPOTESE: ("research", "research"),
               Rotulo.INDICIO: ("indicios", "indicio"), Rotulo.DESCARTADA: ("descartadas", "descartada")}
    for h in ordenadas:
        if h in principais:
            h.status = "principal"
        else:
            lista, status = destino[h.rotulo]
            grupos[lista].append(h)
            h.status = status  # type: ignore[assignment]
    ordem_final = principais + grupos["outras_evidencias"] + grupos["research"] + grupos["indicios"] + grupos["descartadas"]
    for i, h in enumerate(ordem_final, 1):
        h.id = f"H{i}"
    return grupos
