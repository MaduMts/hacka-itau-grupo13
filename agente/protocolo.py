"""Protocolo em rodadas com o agente: catálogo que ele enxerga e validação do que ele publica."""
from __future__ import annotations

from pydantic import ValidationError

from config import MAX_FILTROS
from contratos import SaidaDevin
from motor.carga import BaseAnalitica
from motor.consultas import DESCRICAO_FERRAMENTAS, DISPONIVEIS

_USA = {
    "funil": "segmentar_por, filtros",
    "atrito": "pagina, tipo_atrito (dead_click | rage_click), segmentar_por, filtros",
    "tempo_na_pagina": "pagina, segmentar_por, filtros",
    "sequencia": "evento_a, evento_b, janela_min (minutos), segmentar_por, filtros",
    "temas_nps": "segmentar_por",
}


class ErroProtocolo(ValueError):
    pass


def descrever_catalogo(base: BaseAnalitica, eventos: list[str]) -> str:
    linhas = [
        "Cada consulta tem os campos: ferramenta, segmentar_por, filtros (lista de {dimensao, valor}; "
        f"no máximo {MAX_FILTROS}), pagina, tipo_atrito, evento_a, evento_b, janela_min, motivo. "
        "Campos que não se aplicam à ferramenta vão como null.",
        "",
        "Ferramentas disponíveis:",
    ]
    for nome in DISPONIVEIS:
        linhas.append(f"- {nome}: {DESCRICAO_FERRAMENTAS[nome]} Usa: {_USA[nome]}.")
    linhas += ["", "Dimensões para segmentar_por: nenhum, " + ", ".join(base.dimensoes), "Valores válidos para filtros:"]
    for dim in base.dimensoes:
        linhas.append(f"- {dim}: " + ", ".join(base.valores[dim]))
    if base.tem_fullstory:
        linhas.append("Páginas (atrito): " + ", ".join(base.paginas))
    linhas.append("Eventos da jornada: " + ", ".join(eventos))
    return "\n".join(linhas)


def validar_saida(bruto: dict | None, rodada: int, fase: str) -> SaidaDevin:
    """Converte o structured_output em SaidaDevin, conferindo rodada e fase."""
    if not bruto:
        raise ErroProtocolo(f"O agente não publicou o structured output da rodada {rodada}.")
    try:
        saida = SaidaDevin.model_validate(bruto)
    except ValidationError as e:
        problemas = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        raise ErroProtocolo(f"Structured output da rodada {rodada} fora do formato: {problemas}") from None
    if saida.rodada != rodada or saida.fase != fase:
        raise ErroProtocolo(f"Esperava rodada {rodada} na fase '{fase}', veio rodada {saida.rodada} na fase '{saida.fase}'.")
    return saida
