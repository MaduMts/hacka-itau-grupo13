"""Contratos do MVP: colunas dos CSVs, consultas, resultados, saída do Devin e dossiê.

`SaidaDevin` é enviado ao Devin como `structured_output_schema` (JSON Schema Draft 7).
Por isso ele e seus filhos não usam restrições numéricas nem de tamanho, todo campo é
obrigatório (nulo quando não se aplica) e todo objeto é `extra="forbid"`.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Arquivos de entrada (colunas mínimas do README) ---

COLUNAS_CSV: dict[str, list[str]] = {
    "tagueamento": ["user_id_hash", "session_id", "timestamp", "event_name", "platform", "app_version"],
    "fullstory": ["session_id", "user_id_hash", "timestamp", "page", "event_type", "element", "time_on_page_s"],
    "perfil": ["user_id_hash", "faixa_etaria", "segmento", "plataforma", "tempo_de_conta"],
    "nps": ["user_id_hash", "score", "comentario"],
}
ARQUIVO_OBRIGATORIO = "tagueamento"
ARQUIVOS_OPCIONAIS = ("fullstory", "perfil", "nps")

# Colunas que bloqueiam a entrada do perfil: identificam a pessoa ou são mais finas que faixas.
COLUNAS_PROIBIDAS_PERFIL = (
    "cpf", "cnpj", "rg", "nome", "email", "e_mail", "telefone", "celular",
    "data_nascimento", "data_de_nascimento", "nascimento", "idade",
    "renda", "salario", "patrimonio", "cep", "endereco",
)

# Lacuna declarada quando um arquivo opcional falta.
LACUNA_POR_ARQUIVO = {
    "perfil": "Sem perfil.csv: não dá para dizer quem é afetado (idade, segmento, tempo de conta).",
    "fullstory": "Sem fullstory.csv: não dá para medir atrito na tela (dead click, rage click, tempo).",
    "nps": "Sem nps.csv: sem pista do porquê vinda da voz do cliente.",
}


# --- Consultas do catálogo ---

Ferramenta = Literal["funil", "atrito", "tempo_na_pagina", "sequencia", "temas_nps"]
DimensaoFiltro = Literal["plataforma", "versao_app", "faixa_etaria", "segmento", "tempo_de_conta"]
Dimensao = Literal["nenhum", "plataforma", "versao_app", "faixa_etaria", "segmento", "tempo_de_conta"]
DIMENSOES_PERFIL = ("faixa_etaria", "segmento", "tempo_de_conta")
Metade = Literal["A", "B", "T"]


class Filtro(_Base):
    dimensao: DimensaoFiltro
    valor: str


class PedidoConsulta(_Base):
    """Uma consulta do catálogo pedida pelo agente. Campos que não se aplicam vêm nulos."""

    ferramenta: Ferramenta
    segmentar_por: Dimensao
    filtros: list[Filtro]
    pagina: str | None
    tipo_atrito: Literal["dead_click", "rage_click"] | None
    evento_a: str | None
    evento_b: str | None
    janela_min: int | None
    motivo: str


class LinhaResultado(BaseModel):
    """Uma linha (grupo × métrica) de um resultado agregado."""

    grupo: str
    metrica: str
    n: int
    casos: int
    n_resto: int = 0
    casos_resto: int = 0
    taxa: float = 0.0
    taxa_resto: float | None = None
    lift: float | None = None
    ic95: tuple[float, float] | None = None
    ic99: tuple[float, float] | None = None
    participacao: float | None = None  # fração dos casos da base que caem neste grupo
    casos_extrapolados: int | None = None
    suprimido: bool = False
    extras: dict[str, float | int | str] = Field(default_factory=dict)


class ResultadoConsulta(BaseModel):
    query_id: str
    ferramenta: Ferramenta | Literal["panorama"]
    params: dict[str, Any]
    metade: Metade
    fonte: Literal["tagueamento", "fullstory", "nps", "misto"]
    fator_extrapolacao: float = 1.0
    linhas: list[LinhaResultado] = Field(default_factory=list)
    geral: dict[str, float | int | str] = Field(default_factory=dict)
    avisos: list[str] = Field(default_factory=list)
    texto_llm: str = ""
    sql: str = ""
    duracao_ms: float = 0.0


# --- Saída do agente (structured_output do Devin) ---


class RefEvidencia(_Base):
    query_id: str  # consulta da metade A, ex. "Q05-A"
    grupo: str | None  # linha do resultado; nulo = população inteira
    metrica: str  # ex. "dead_click", "abandono:lock_confirm", "lento", "sequencia"


class Candidata(_Base):
    tipo: Literal["padrao_observado", "explicacao_causal"]
    titulo: str
    enunciado: str  # o quê / quem / quanto, sem "porque"
    evidencia_principal: RefEvidencia
    query_ids_citados: list[str]
    o_que_o_dado_responde: str
    o_que_so_a_research_responde: str
    pergunta_para_research: str


class SaidaDevin(_Base):
    """O que o agente escreve no structured_output a cada rodada."""

    rodada: int
    fase: Literal["plano", "candidatas"]
    consultas: list[PedidoConsulta]  # preenchido na fase "plano"
    candidatas: list[Candidata]  # preenchido na fase "candidatas"
    resposta_ao_palpite: str | None
    lacunas: list[str]


# --- Resultado da execução e dossiê ---


class Rotulo(str, Enum):
    EVIDENCIA = "Evidência"
    INDICIO = "Indício"
    HIPOTESE = "Hipótese"
    LACUNA = "Lacuna"
    DESCARTADA = "Descartada"  # status do código (não é rótulo do README): não vira insight


class Verificacao(BaseModel):
    conferidos: list[str] = Field(default_factory=list)
    orfaos: list[str] = Field(default_factory=list)
    refs_invalidas: list[str] = Field(default_factory=list)
    avisos_causais: list[str] = Field(default_factory=list)
    refeita: bool = False
    verificada: bool = True


class Hipotese(BaseModel):
    id: str
    candidata: Candidata
    familia: Literal["comparativa", "comportamental"]
    fonte: Literal["tagueamento", "fullstory", "nps"]
    dimensao: str | None = None  # segmentar_por da consulta principal
    linha_a: LinhaResultado | None = None
    linha_b: LinhaResultado | None = None
    linha_t: LinhaResultado | None = None
    query_ids: dict[str, str] = Field(default_factory=dict)  # {"A": "Q03-A", "B": "Q03-B", "T": "Q03-T"}
    rotulo: Rotulo
    regra: str
    motivo: str
    status: Literal["principal", "evidencia_extra", "research", "indicio", "descartada", "lacuna"]
    verificacao: Verificacao = Field(default_factory=Verificacao)
    usuarios_afetados: int | None = None
    extrapolado: bool = False
    impacto: float = 0.0
    evidencias_complementares: list[str] = Field(default_factory=list)


class DecisaoPM(BaseModel):
    hipotese_id: str
    acao: Literal["aceitar", "editar", "rejeitar", "mais_analise"]
    pm: str
    comentario: str = ""
    textos_editados: dict[str, str] = Field(default_factory=dict)
    ts: str
    verificacao_edicao: Verificacao | None = None


class EntradaPM(BaseModel):
    squad: str
    jornada: str
    periodo_inicio: date
    periodo_fim: date
    palpite: str | None = None
    pm: str
    fonte_dados: str  # "exemplo:normal" | "exemplo:incompleta" | "exemplo:incorreta" | "upload"


class MetaExecucao(BaseModel):
    run_id: str
    parent_run_id: str | None = None
    iniciado_em: str
    squad: str
    contexto_sha: str
    prompt_versao: str
    prompt_sha: str
    llm: Literal["devin-v1", "gravado", "roteirizado"]
    devin_session_id: str | None = None
    devin_url: str | None = None
    dados_hash: str
    semente: int | None = None


class Dossie(BaseModel):
    meta: MetaExecucao
    entrada: EntradaPM
    principais: list[Hipotese] = Field(default_factory=list)
    outras_evidencias: list[Hipotese] = Field(default_factory=list)
    research: list[Hipotese] = Field(default_factory=list)
    indicios: list[Hipotese] = Field(default_factory=list)
    descartadas: list[Hipotese] = Field(default_factory=list)
    lacunas: list[str] = Field(default_factory=list)
    resposta_ao_palpite: str | None = None
    avisos: list[str] = Field(default_factory=list)
    decisoes: list[DecisaoPM] = Field(default_factory=list)


# --- JSON Schema para o Devin ---


def _resolver_refs(no: Any, defs: dict[str, Any]) -> Any:
    if isinstance(no, dict):
        if "$ref" in no:
            nome = no["$ref"].split("/")[-1]
            return _resolver_refs(copy.deepcopy(defs[nome]), defs)
        return {k: _resolver_refs(v, defs) for k, v in no.items() if k not in ("title", "$defs")}
    if isinstance(no, list):
        return [_resolver_refs(item, defs) for item in no]
    return no


def schema_devin(modelo: type[BaseModel] = SaidaDevin) -> dict[str, Any]:
    """JSON Schema Draft 7 autocontido (sem $ref) do modelo, para `structured_output_schema`."""
    bruto = modelo.model_json_schema()
    schema = _resolver_refs(bruto, bruto.get("$defs", {}))
    return {"$schema": "http://json-schema.org/draft-07/schema#", **schema}


def tamanho_schema_bytes(schema: dict[str, Any]) -> int:
    return len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
