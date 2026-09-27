"""Modo demo: grava uma execução real do agente e a reproduz sem rede.

A gravação guarda só o que o agente publicou em cada rodada (structured output), com os
tempos. No replay, o nosso código executa as consultas de verdade sobre os mesmos dados
sintéticos, e as respostas do agente vêm da gravação. Se os dados forem outros, o replay
se recusa a rodar: os números do dossiê não bateriam com o que o agente escreveu.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agente.cliente_devin import ClienteRoteirizado, ErroDevin
from config import RAIZ

ARQUIVO_DEMO = RAIZ / "demo" / "execucao_gravada.json"
VERSAO_GRAVACAO = 1


def gravacao_de(exe: Any) -> dict[str, Any]:
    """Monta a gravação a partir de uma Execucao ao vivo que terminou sem erro."""
    eventos = exe.registro.ler()
    inicio = next(e for e in eventos if e["evento"] == "execucao_iniciada")["dados"]
    rodadas = [{"rodada": e["dados"]["rodada"], "segundos": e["dados"]["segundos"],
                "structured_output": e["dados"]["structured_output"]}
               for e in eventos if e["evento"] == "rodada_publicada"]
    d = exe.dossie
    return {
        "versao": VERSAO_GRAVACAO,
        "gravado_em": d.meta.iniciado_em,
        "run_id_original": exe.run_id,
        "llm_original": d.meta.llm,
        "sessao_original": d.meta.devin_url,
        "prompt_versao": d.meta.prompt_versao,
        "prompt_sha": d.meta.prompt_sha,
        "contexto_sha": d.meta.contexto_sha,
        "dados_hash": d.meta.dados_hash,
        "entrada": inicio["entrada"],
        "rodadas": rodadas,
    }


def salvar(gravacao: dict[str, Any], caminho: Path = ARQUIVO_DEMO) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(gravacao, ensure_ascii=False, indent=2), encoding="utf-8")
    return caminho


def carregar(caminho: Path = ARQUIVO_DEMO) -> dict[str, Any] | None:
    if not caminho.exists():
        return None
    gravacao = json.loads(caminho.read_text(encoding="utf-8"))
    return gravacao if gravacao.get("versao") == VERSAO_GRAVACAO and gravacao.get("rodadas") else None


class ClienteGravado(ClienteRoteirizado):
    """Reproduz as rodadas gravadas. `atraso_s` simula a espera, para a demo mostrar o progresso."""

    llm = "gravado"

    def __init__(self, gravacao: dict[str, Any], atraso_s: float = 1.5):
        super().__init__(self._roteiro, sessao_id=f"gravacao-{gravacao.get('run_id_original', 'demo')}")
        self.gravacao = gravacao
        self.atraso_s = atraso_s
        self._por_rodada = {r["rodada"]: r["structured_output"] for r in gravacao["rodadas"]}

    def criar_sessao(self, prompt: str, **kwargs: Any) -> tuple[str, str]:
        base = getattr(self.registro, "base", None)
        if base is not None and base.dados_hash != self.gravacao["dados_hash"]:
            raise ErroDevin("gravacao", "A execução gravada foi feita com outros dados. Use os dados de exemplo "
                                        "(cenário normal) ou regrave a demo com scripts/gravar_demo.py.")
        return super().criar_sessao(prompt, **kwargs)

    def _roteiro(self, rodada: int, textos: list[str], registro: Any) -> dict[str, Any]:
        if self.atraso_s:
            time.sleep(self.atraso_s)
        if rodada in self._por_rodada:
            return self._por_rodada[rodada]
        # O código pediu uma correção que a gravação não tem: repete as últimas candidatas gravadas.
        ultima = self._por_rodada[max(self._por_rodada)]
        return {**ultima, "rodada": rodada}
