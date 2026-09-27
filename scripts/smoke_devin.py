"""Smoke test da API do Devin (Fase 0).

Uso:
    python scripts/smoke_devin.py            # só valida a chave (GET /sessions; não cria sessão)
    python scripts/smoke_devin.py --sessao   # + 1 sessão mínima com 2 rodadas (consome um pouco da cota)

Mede o tempo de cada rodada, confere o structured_output e encerra a sessão no fim.
Nunca imprime a chave. Salva um relatório em data/gerados/smoke_devin_<data>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.cliente_devin import ClienteDevin, ErroDevin, EstadoSessao  # noqa: E402
from config import DEVIN_MAX_ACU, PASTA_DADOS, chave_devin  # noqa: E402

SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "rodada": {"type": "integer"},
        "fase": {"type": "string", "enum": ["plano", "candidatas"]},
        "itens": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rodada", "fase", "itens"],
    "additionalProperties": False,
}

PROMPT = """Este é um teste técnico de integração via API. Regras:
- NÃO use terminal, navegador, editor nem repositórios, e não instale nada. Não pesquise na internet.
- Responda SOMENTE atualizando o structured output. Não precisa escrever mensagem.

Rodada 1: atualize o structured output para exatamente
{"rodada": 1, "fase": "plano", "itens": ["consulta_a", "consulta_b"]}
Depois disso, pare e aguarde a minha próxima mensagem."""

MENSAGEM_RODADA_2 = """Rodada 2: atualize o structured output para exatamente
{"rodada": 2, "fase": "candidatas", "itens": ["hipotese_1"]}
Depois disso, pare e aguarde. Não faça mais nada."""

CUTUCADA = "Lembrete: publique a rodada {n} no structured output exatamente como pedido e depois aguarde."


def _registrar_transicoes(eventos: list[dict]):
    ultimo = {"status": None}

    def ao_consultar(est: EstadoSessao, decorrido: float) -> None:
        if est.status_enum != ultimo["status"]:
            ultimo["status"] = est.status_enum
            eventos.append({"t": round(decorrido, 1), "status_enum": est.status_enum, "rodada_publicada": est.rodada})
            print(f"      {decorrido:6.1f}s  status={est.status_enum}  rodada publicada={est.rodada}")

    return ao_consultar


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # terminal do Windows
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessao", action="store_true", help="cria 1 sessão mínima com 2 rodadas")
    parser.add_argument("--max-acu", type=int, default=DEVIN_MAX_ACU)
    args = parser.parse_args()

    cliente = ClienteDevin(chave_devin())
    relatorio: dict = {"iniciado_em": datetime.now().astimezone().isoformat(timespec="seconds"), "sessao": None}

    try:
        sessoes = cliente.listar_sessoes(1)
        relatorio["chave_valida"] = True
        print(f"[ok] Chave válida: GET /sessions respondeu ({len(sessoes)} sessão listada).")
    except ErroDevin as e:
        print(f"[erro] {e.tipo}: {e}")
        return 1

    if not args.sessao:
        return 0

    sid = None
    rodadas: list[dict] = []
    t0 = time.monotonic()
    try:
        sid, url = cliente.criar_sessao(
            PROMPT, schema=SCHEMA, titulo="smoke dossie g13", tags=["dossie-hipoteses", "smoke"], max_acu=args.max_acu
        )
        relatorio["sessao"] = {"session_id": sid, "url": url}
        print(f"[ok] Sessão criada em {time.monotonic() - t0:.1f}s: {url}")

        for n, mensagem in ((1, None), (2, MENSAGEM_RODADA_2)):
            t = time.monotonic()
            if mensagem:
                cliente.enviar_mensagem(sid, mensagem)
            print(f"   rodada {n}:")
            eventos: list[dict] = []
            est = cliente.aguardar_rodada(sid, n, cutucada=CUTUCADA.format(n=n), ao_consultar=_registrar_transicoes(eventos))
            duracao = time.monotonic() - t
            esperado = {"rodada": n, "fase": "plano" if n == 1 else "candidatas"}
            confere = all(est.structured_output.get(k) == v for k, v in esperado.items())
            rodadas.append({"rodada": n, "segundos": round(duracao, 1), "status_final": est.status_enum,
                            "structured_output": est.structured_output, "confere": confere, "transicoes": eventos})
            print(f"[ok] Rodada {n} em {duracao:.1f}s · status={est.status_enum} · confere com o pedido: {confere}")
    except ErroDevin as e:
        relatorio["erro"] = {"tipo": e.tipo, "mensagem": str(e)}
        print(f"[erro] {e.tipo}: {e}")
    finally:
        if sid:
            try:
                cliente.encerrar(sid)
                print("[ok] Sessão encerrada.")
            except ErroDevin as e:
                print(f"[aviso] Não consegui encerrar a sessão ({e.tipo}). Encerre pelo app do Devin.")

    relatorio["rodadas"] = rodadas
    relatorio["total_segundos"] = round(time.monotonic() - t0, 1)
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    destino = PASTA_DADOS / f"smoke_devin_{datetime.now():%Y%m%d-%H%M%S}.json"
    destino.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Total: {relatorio['total_segundos']}s · relatório: {destino}")
    print("Consumo da sessão: veja 'Session Insights' no app do Devin (link acima).")
    return 0 if rodadas and all(r["confere"] for r in rodadas) and len(rodadas) == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
