"""Refutação: re-executa na metade B (e na base toda) exatamente a consulta que sustenta a candidata.

O agente nunca vê a metade B. A réplica usa a mesma especificação guardada no registro,
então não há escolha a posteriori de recorte.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from contratos import Candidata, LinhaResultado
from motor.registro_consultas import RegistroConsultas


@dataclass
class Replica:
    linha_a: LinhaResultado | None = None
    linha_b: LinhaResultado | None = None
    linha_t: LinhaResultado | None = None
    query_ids: dict[str, str] = field(default_factory=dict)
    familia: str = "comparativa"  # "comportamental" para sequências: mostram quanto, não por quê
    fonte: str = "tagueamento"
    segmentar_por: str | None = None
    fator_extrapolacao: float = 1.0


def refutar(c: Candidata, registro: RegistroConsultas) -> Replica:
    ref = c.evidencia_principal
    res_a = registro.obter(ref.query_id)
    if res_a is None or not ref.query_id.endswith("-A"):
        return Replica()
    res_b = registro.replicar(ref.query_id, "B")
    res_t = registro.replicar(ref.query_id, "T")
    return Replica(
        linha_a=registro.linha(ref.query_id, ref.grupo, ref.metrica),
        linha_b=registro.linha(res_b.query_id, ref.grupo, ref.metrica),
        linha_t=registro.linha(res_t.query_id, ref.grupo, ref.metrica),
        query_ids={"A": ref.query_id, "B": res_b.query_id, "T": res_t.query_id},
        familia="comportamental" if res_a.ferramenta == "sequencia" else "comparativa",
        fonte=res_a.fonte if res_a.fonte in ("fullstory", "nps") else "tagueamento",
        segmentar_por=res_a.params.get("segmentar_por"),
        fator_extrapolacao=res_a.fator_extrapolacao,
    )
