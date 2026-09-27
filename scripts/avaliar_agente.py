"""Avalia o agente contra o gabarito dos dados sintéticos.

Uso:
    python scripts/avaliar_agente.py --execucoes 3             # Devin ao vivo (gasta cota)
    python scripts/avaliar_agente.py --modo roteirizado        # sem rede, para conferir o script

Cada execução roda o pipeline completo no dataset de exemplo, confere o gabarito (P1, P2, P3,
T4, T5) e mede o tempo de cada rodada. Salva um relatório em data/gerados/avaliacao_<data>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.cliente_devin import ClienteDevin, ClienteRoteirizado, ErroDevin  # noqa: E402
from agente.roteiros import roteiro_gabarito  # noqa: E402
from config import PASTA_DADOS, chave_devin  # noqa: E402
from contexto.carregar import carregar_contexto  # noqa: E402
from contratos import EntradaPM  # noqa: E402
from entrada.checagem import checar_entrada  # noqa: E402
from gabarito import conferir  # noqa: E402
from gerador.gerar_dados import garantir_exemplo  # noqa: E402
from motor.carga import localizar_arquivos  # noqa: E402
from pipeline import rodar_execucao  # noqa: E402

PALPITE_DEMO = "Acho que idosos travam na confirmação"
PERIODO = (date(2026, 8, 1), date(2026, 8, 30))


def uma_execucao(cliente, variante: str, palpite: str, n: int) -> dict:
    ctx = carregar_contexto("cartoes")
    arquivos = localizar_arquivos(garantir_exemplo(variante))
    chk = checar_entrada(arquivos, ctx, "bloqueio_desbloqueio", *PERIODO, palpite)
    entrada = EntradaPM(squad="cartoes", jornada="bloqueio_desbloqueio", periodo_inicio=PERIODO[0], periodo_fim=PERIODO[1],
                        palpite=palpite, pm="avaliação automática", fonte_dados=f"exemplo:{variante}")
    inicio = time.monotonic()

    def progresso(etapa: str, mensagem: str) -> None:
        if etapa != "agente" or mensagem.endswith(("0 s", "5 s")):  # status do agente a cada ~5 s
            print(f"   [{n}] {time.monotonic() - inicio:6.1f}s  {etapa:11} {mensagem}", flush=True)

    exe = rodar_execucao(entrada, chk, ctx, cliente, progresso)
    total = round(time.monotonic() - inicio, 1)
    relato: dict = {"execucao": n, "run_id": exe.run_id, "erro": exe.erro, "erro_tipo": exe.erro_tipo, "segundos": total}
    if exe.agente:
        relato.update(sessao=exe.agente.url, rodadas_s=exe.agente.duracoes, consultas=len(exe.agente.executadas),
                      consultas_invalidas=exe.agente.erros_consulta)
    if exe.dossie:
        d = exe.dossie
        todas = d.principais + d.outras_evidencias + d.research + d.indicios + d.descartadas
        relato["correcao_pedida"] = any(e["evento"] == "problemas_encontrados" for e in exe.registro.ler())
        relato["hipoteses"] = [{"id": h.id, "rotulo": h.rotulo.value, "regra": h.regra, "grupo": h.candidata.evidencia_principal.grupo,
                                "metrica": h.candidata.evidencia_principal.metrica, "verificada": h.verificacao.verificada,
                                "titulo": h.candidata.titulo} for h in todas]
        relato["gabarito"] = {i.id: {"ok": i.ok, "encontrado": i.encontrado} for i in conferir(d, tem_perfil=variante != "incompleta")}
        relato["resposta_ao_palpite"] = d.resposta_ao_palpite
    return relato


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execucoes", type=int, default=1)
    parser.add_argument("--modo", choices=["ao_vivo", "roteirizado"], default="ao_vivo")
    parser.add_argument("--variante", choices=["normal", "incompleta"], default="normal")
    parser.add_argument("--palpite", default=PALPITE_DEMO)
    args = parser.parse_args()

    relatos = []
    for n in range(1, args.execucoes + 1):
        try:
            cliente = ClienteDevin(chave_devin()) if args.modo == "ao_vivo" else ClienteRoteirizado(roteiro_gabarito)
        except ErroDevin as e:
            print(f"[erro] {e}")
            return 2
        print(f"== execução {n}/{args.execucoes} · {args.modo} · {args.variante}", flush=True)
        relato = uma_execucao(cliente, args.variante, args.palpite, n)
        relatos.append(relato)
        if relato["erro"]:
            print(f"   [{n}] ERRO ({relato['erro_tipo']}): {relato['erro']}")
            continue
        itens = relato["gabarito"]
        print(f"   [{n}] {relato['segundos']} s · rodadas {relato.get('rodadas_s')} · {relato.get('consultas')} consultas · "
              f"correção pedida: {relato.get('correcao_pedida')}")
        print("   [{}] gabarito: {}".format(n, "  ".join(f"{k} {'✅' if v['ok'] else '❌'}" for k, v in itens.items())))

    ok = [r for r in relatos if not r["erro"]]
    resumo = {
        "modo": args.modo, "variante": args.variante, "execucoes": len(relatos), "sem_erro": len(ok),
        "acertos": {k: sum(r["gabarito"][k]["ok"] for r in ok) for k in (ok[0]["gabarito"] if ok else {})},
        "segundos_medio": round(sum(r["segundos"] for r in ok) / len(ok), 1) if ok else None,
    }
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    destino = PASTA_DADOS / f"avaliacao_{datetime.now():%Y%m%d-%H%M%S}.json"
    destino.write_text(json.dumps({"resumo": resumo, "execucoes": relatos}, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
    print(f"\nResumo: {json.dumps(resumo, ensure_ascii=False)}\nRelatório: {destino}")
    return 0 if ok and all(all(v["ok"] for v in r["gabarito"].values()) for r in ok) else 1


if __name__ == "__main__":
    sys.exit(main())
