"""Registro append-only de cada execução (JSONL em `registros/<run_id>.jsonl`).

Guarda quem fez o quê e quando: agente, código, verificador, PM e sistema, com versão do
contexto e do prompt para reproduzir qualquer resultado. Não há operação de apagar:
a PM decide, mas não remove o registro do que o agente propôs.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from config import PASTA_REGISTROS

Ator = Literal["sistema", "codigo", "agente", "verificador", "pm"]


def agora_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def novo_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class Registro:
    def __init__(self, run_id: str, pasta: Path = PASTA_REGISTROS):
        self.run_id = run_id
        self.caminho = pasta / f"{run_id}.jsonl"
        pasta.mkdir(parents=True, exist_ok=True)
        self._seq = len(self.ler())

    def evento(self, ator: Ator, evento: str, **dados: Any) -> dict[str, Any]:
        self._seq += 1
        linha = {"ts": agora_iso(), "run_id": self.run_id, "seq": self._seq, "ator": ator, "evento": evento, "dados": dados}
        with self.caminho.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
        return linha

    def ler(self) -> list[dict[str, Any]]:
        if not self.caminho.exists():
            return []
        with self.caminho.open(encoding="utf-8") as f:
            return [json.loads(linha) for linha in f if linha.strip()]

    def como_jsonl(self) -> str:
        return self.caminho.read_text(encoding="utf-8") if self.caminho.exists() else ""
