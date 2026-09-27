"""Estatística das consultas: taxa, lift contra o resto, IC (log-RR de Katz) e formatação pt-BR.

Toda métrica é uma taxa de problema (abandono, erro, dead click...). Lift = taxa do grupo
dividida pela taxa do resto dos usuários no mesmo recorte. Com contagem zero, aplica a
correção de Haldane (+0,5 nos casos, +1 no n) para o IC não explodir.
"""
from __future__ import annotations

import math

from config import LIFT_MIN_A, N_MIN_EVIDENCIA, N_MIN_EXIBICAO, Z_95, Z_99
from contratos import LinhaResultado


def lift_ic(casos_g: int, n_g: int, casos_r: int, n_r: int, z: float) -> tuple[float, float, float] | None:
    """Lift (risco relativo) do grupo contra o resto, com IC. None se algum lado está vazio."""
    if n_g <= 0 or n_r <= 0:
        return None
    a, b, c, d = float(casos_g), float(n_g), float(casos_r), float(n_r)
    if a == 0 or c == 0 or a == b or c == d:
        a, c, b, d = a + 0.5, c + 0.5, b + 1.0, d + 1.0
    rr = (a / b) / (c / d)
    ep = math.sqrt(max(1 / a - 1 / b + 1 / c - 1 / d, 0.0))
    return rr, math.exp(math.log(rr) - z * ep), math.exp(math.log(rr) + z * ep)


def montar_linhas(
    metrica: str,
    grupos: list[tuple[str, int, int]],
    total_n: int,
    total_casos: int,
    fator_extrapolacao: float = 1.0,
) -> list[LinhaResultado]:
    """Uma linha por grupo (grupo, n, casos), com resto = total − grupo. Grupos < 50 ficam suprimidos."""
    linhas = []
    for grupo, n, casos in grupos:
        n_r, c_r = total_n - n, total_casos - casos
        l95 = lift_ic(casos, n, c_r, n_r, Z_95)
        l99 = lift_ic(casos, n, c_r, n_r, Z_99)
        linhas.append(LinhaResultado(
            grupo=grupo, metrica=metrica, n=n, casos=casos, n_resto=n_r, casos_resto=c_r,
            taxa=casos / n if n else 0.0,
            taxa_resto=(c_r / n_r) if n_r > 0 else None,
            lift=l95[0] if l95 else None,
            ic95=(l95[1], l95[2]) if l95 else None,
            ic99=(l99[1], l99[2]) if l99 else None,
            participacao=(casos / total_casos) if total_casos else None,
            casos_extrapolados=round(casos * fator_extrapolacao) if fator_extrapolacao != 1.0 else None,
            suprimido=n < N_MIN_EXIBICAO,
        ))
    return linhas


def destaque(linha: LinhaResultado) -> bool:
    """▲ na tabela: efeito grande, IC acima de 1 e n suficiente para virar evidência."""
    return (
        not linha.suprimido and linha.lift is not None and linha.ic95 is not None
        and linha.lift >= LIFT_MIN_A and linha.ic95[0] > 1.0
        and min(linha.n, linha.n_resto) >= N_MIN_EVIDENCIA
    )


# --- Formatação pt-BR (vírgula decimal, ponto de milhar) ---


def fmt_int(valor: float | int) -> str:
    return f"{round(valor):,}".replace(",", ".")


def fmt_num(valor: float, casas: int = 2) -> str:
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_pct(fracao: float | None, casas: int = 1) -> str:
    return "—" if fracao is None else fmt_num(100 * fracao, casas) + "%"


def fmt_lift(linha: LinhaResultado) -> str:
    if linha.lift is None or linha.ic95 is None:
        return "—"
    return f"{fmt_num(linha.lift)} ({fmt_num(linha.ic95[0])}–{fmt_num(linha.ic95[1])})"
