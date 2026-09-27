"""Memória da squad: as decisões recentes da PM, lidas do registro, entram no contexto do agente.

Assim o agente não apresenta como novidade o que a squad já decidiu. Só entram título,
rótulo, decisão, comentário e data (nada de dado de cliente).
"""
from __future__ import annotations

import json
from pathlib import Path

import config

_ACAO = {"aceitar": "aceita", "editar": "aceita com edição", "rejeitar": "rejeitada", "mais_analise": "pediu mais análise"}


def decisoes_recentes(squad: str, limite: int = 5, pasta: Path | None = None, ignorar_run: str | None = None) -> list[dict]:
    pasta = pasta or config.PASTA_REGISTROS
    if not pasta.exists():
        return []
    decisoes = []
    for arquivo in sorted(pasta.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        if ignorar_run and arquivo.stem == ignorar_run:
            continue
        try:
            eventos = [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        inicio = next((e for e in eventos if e.get("evento") == "execucao_iniciada"), None)
        if not inicio or inicio["dados"].get("squad") != squad:
            continue
        for e in eventos:
            if e.get("evento") == "decisao" and e.get("ator") == "pm":
                d = e["dados"]
                decisoes.append({"quando": e["ts"], "titulo": d.get("titulo", ""), "rotulo": d.get("rotulo", ""),
                                 "acao": d.get("acao", ""), "comentario": d.get("comentario", "")})
    decisoes.sort(key=lambda d: d["quando"], reverse=True)
    return decisoes[:limite]


def texto_memoria(decisoes: list[dict]) -> list[str]:
    linhas = []
    for d in decisoes:
        comentario = f": {d['comentario']}" if d["comentario"] else ""
        linhas.append(f"{d['quando'][:10]} · “{d['titulo']}” ({d['rotulo']}) · {_ACAO.get(d['acao'], d['acao'])}{comentario}")
    return linhas
