"""Confere o dossiê contra o gabarito dos dados sintéticos (só faz sentido com os dados de exemplo).

Mostra para a banca, na própria tela, que o agente achou o que foi plantado (P1, P2, P3) e que o
código não deixou as armadilhas (T4, T5) virarem evidência.
"""
from __future__ import annotations

from dataclasses import dataclass

from contratos import Dossie, Hipotese, Rotulo
from gerador.padroes import GABARITO


@dataclass
class ItemConferencia:
    id: str
    descricao: str
    esperado: str
    encontrado: str
    ok: bool


def _todas(d: Dossie) -> list[Hipotese]:
    return d.principais + d.outras_evidencias + d.research + d.indicios + d.descartadas


def _descrever(h: Hipotese) -> str:
    return f"{h.id} · {h.rotulo.value} (regra {h.regra})"


def conferir(d: Dossie, tem_perfil: bool = True) -> list[ItemConferencia]:
    todas = _todas(d)
    itens = []
    for alvo in GABARITO:
        grupo = alvo["grupo"]
        do_grupo = [h for h in todas if h.candidata.evidencia_principal.grupo == grupo] if grupo else []
        if alvo["id"] in ("P1", "P2"):
            if alvo["dimensao"] == "faixa_etaria" and not tem_perfil:
                lacuna = any("quem" in l for l in d.lacunas)
                itens.append(ItemConferencia(alvo["id"], alvo["descricao"], "Lacuna (sem perfil)",
                                             "lacuna declarada" if lacuna else "lacuna não declarada", lacuna))
                continue
            evidencias = [h for h in do_grupo if h.rotulo == Rotulo.EVIDENCIA]
            encontrado = _descrever(evidencias[0]) if evidencias else (_descrever(do_grupo[0]) if do_grupo else "não encontrado")
            itens.append(ItemConferencia(alvo["id"], alvo["descricao"], "Evidência", encontrado, bool(evidencias)))
        elif alvo["id"] == "P3":
            seq = [h for h in todas if h.candidata.evidencia_principal.metrica == "sequencia"]
            hipoteses = [h for h in seq if h.rotulo == Rotulo.HIPOTESE]
            encontrado = _descrever(hipoteses[0]) if hipoteses else (_descrever(seq[0]) if seq else "não encontrado")
            itens.append(ItemConferencia(alvo["id"], alvo["descricao"], "Hipótese para a Research", encontrado, bool(hipoteses)))
        else:  # armadilhas: não podem virar evidência
            evidencia = [h for h in do_grupo if h.rotulo == Rotulo.EVIDENCIA]
            encontrado = ", ".join(_descrever(h) for h in do_grupo) if do_grupo else "não proposto pelo agente"
            itens.append(ItemConferencia(alvo["id"], alvo["descricao"], alvo["esperado"], encontrado, not evidencia))
    return itens
