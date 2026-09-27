"""Textos enviados ao agente em cada rodada.

Tudo é montado de forma determinística a partir do procedimento versionado, do contexto da
squad e dos resultados das consultas. `prompt_sha` identifica a versão exata no registro.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from config import ORCAMENTO_CONSULTAS, PROMPT_VERSAO
from contratos import EntradaPM, ResultadoConsulta, schema_devin

PROCEDIMENTO = (Path(__file__).parent / "procedimento.md").read_text(encoding="utf-8")


def prompt_sha() -> str:
    material = PROMPT_VERSAO + PROCEDIMENTO + json.dumps(schema_devin(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def descrever_palpite(palpite: str | None, status: str) -> str:
    if not palpite or status == "sem_palpite":
        return "sem palpite: explore a jornada de forma aberta."
    if status == "vago":
        return (f"\"{palpite}\" (vago: não cita etapa, página, métrica ou grupo). Explore de forma aberta e diga isso "
                "na resposta ao palpite.")
    return f"\"{palpite}\""


def prompt_rodada_1(
    texto_contexto: str,
    entrada: EntradaPM,
    nome_jornada: str,
    palpite_status: str,
    lacunas: list[str],
    panorama: ResultadoConsulta,
    catalogo: str,
    orcamento: int = ORCAMENTO_CONSULTAS,
) -> str:
    lacunas_txt = "\n".join(f"- {l}" for l in lacunas) if lacunas else "- nenhuma"
    return "\n".join([
        PROCEDIMENTO.replace("{orcamento}", str(orcamento)),
        "",
        "# Contexto (carregado automaticamente da squad)",
        texto_contexto,
        "",
        "# Pergunta da PM",
        f"- Jornada: {nome_jornada}",
        f"- Período: {entrada.periodo_inicio:%d/%m/%Y} a {entrada.periodo_fim:%d/%m/%Y}",
        f"- Palpite: {descrever_palpite(entrada.palpite, palpite_status)}",
        "- Lacunas já declaradas pela checagem dos dados:",
        lacunas_txt,
        "",
        "# Panorama dos dados (metade A)",
        panorama.texto_llm,
        "",
        "# Catálogo de consultas",
        catalogo,
        "",
        "# Rodada 1",
        f"Publique no structured output: rodada=1, fase=\"plano\", consultas=[até {orcamento}], candidatas=[], "
        "resposta_ao_palpite=null, lacunas=[]. Depois pare e aguarde a próxima mensagem.",
    ])


def mensagem_rodada_2(resultados: list[ResultadoConsulta], erros: list[str]) -> str:
    partes = ["# Resultados da rodada 1 (metade A)", ""]
    for res in resultados:
        partes += [res.texto_llm, ""]
    if erros:
        partes += ["Consultas não executadas:"] + [f"- {e}" for e in erros] + [""]
    partes += [
        "# Rodada 2",
        "Redija de 3 a 4 candidatas seguindo o procedimento. Publique no structured output: rodada=2, "
        "fase=\"candidatas\", consultas=[], candidatas=[...], resposta_ao_palpite e lacunas. Depois pare e aguarde.",
    ]
    return "\n".join(partes)


def mensagem_correcao(rodada: int, problemas: list[str]) -> str:
    return "\n".join([
        f"# Rodada {rodada}: correção pedida pelo verificador de números",
        "O verificador encontrou estes problemas:",
        *[f"- {p}" for p in problemas],
        "",
        "Reescreva as candidatas corrigindo só esses pontos. Use apenas números das tabelas das consultas citadas, "
        "no mesmo formato, e cite o query_id. Explicações causais vão no campo da Research, não no enunciado.",
        f"Publique no structured output: rodada={rodada}, fase=\"candidatas\", com a lista completa de candidatas. "
        "Depois pare e aguarde.",
    ])


def cutucada(rodada: int) -> str:
    return (f"Lembrete: publique a rodada {rodada} no structured output (campo rodada={rodada}) conforme o "
            "procedimento e depois aguarde. Não é preciso escrever no chat.")
