"""Grava a execução da demo: roda o Devin ao vivo com a entrada da demo e salva as rodadas.

Uso:
    python scripts/gravar_demo.py

Só salva em demo/execucao_gravada.json se o gabarito passar inteiro (P1, P2, P3, T4, T5).
Regrave sempre que mudar o procedimento, o contexto ou os dados (o teste de replay avisa).
Gasta uma sessão da cota do Devin.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.cliente_devin import ClienteDevin, ErroDevin  # noqa: E402
from agente.gravacao import ARQUIVO_DEMO, gravacao_de, salvar  # noqa: E402
from config import chave_devin  # noqa: E402
from contexto.carregar import carregar_contexto  # noqa: E402
from contratos import EntradaPM  # noqa: E402
from entrada.checagem import checar_entrada  # noqa: E402
from gabarito import conferir  # noqa: E402
from gerador.gerar_dados import garantir_exemplo  # noqa: E402
from motor.carga import localizar_arquivos  # noqa: E402
from pipeline import rodar_execucao  # noqa: E402

ENTRADA_DEMO = {"squad": "cartoes", "jornada": "bloqueio_desbloqueio", "periodo_inicio": date(2026, 8, 1),
                "periodo_fim": date(2026, 8, 30), "palpite": "Acho que idosos travam na confirmação",
                "pm": "PM da Squad Cartões", "fonte_dados": "exemplo:normal"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        cliente = ClienteDevin(chave_devin())
    except ErroDevin as e:
        print(f"[erro] {e}")
        return 2
    ctx = carregar_contexto("cartoes")
    entrada = EntradaPM(**ENTRADA_DEMO)
    chk = checar_entrada(localizar_arquivos(garantir_exemplo("normal")), ctx, entrada.jornada, entrada.periodo_inicio,
                         entrada.periodo_fim, entrada.palpite)
    exe = rodar_execucao(entrada, chk, ctx, cliente, lambda etapa, msg: print(f"  {etapa:11} {msg}", flush=True))
    if exe.erro:
        print(f"[erro] {exe.erro_tipo}: {exe.erro}")
        return 1
    itens = conferir(exe.dossie, tem_perfil=True)
    print("gabarito: " + "  ".join(f"{i.id} {'✅' if i.ok else '❌'}" for i in itens))
    if not all(i.ok for i in itens):
        print("[aviso] O gabarito não passou inteiro: a gravação NÃO foi salva. Rode de novo.")
        return 1
    destino = salvar(gravacao_de(exe))
    print(f"[ok] Demo gravada em {destino} (rodadas: {exe.agente.duracoes}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
