"""Checagem da entrada, antes de qualquer LLM: bloqueia com mensagem clara ou declara lacunas.

Bloqueia: falta tagueamento.csv, falta coluna, mais de 1% de timestamps inválidos, período sem
eventos, identificador com cara de dado pessoal, perfil com coluna proibida (CPF, nome, renda...).
Declara lacuna: arquivo opcional ausente (sem perfil → "modo comportamental").
Classifica o palpite: específico (cita etapa, página, métrica ou grupo), vago ou ausente.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from contexto.carregar import ContextoSquad
from contratos import ARQUIVO_OBRIGATORIO, COLUNAS_CSV, COLUNAS_PROIBIDAS_PERFIL, LACUNA_POR_ARQUIVO
from motor.carga import abrir_brutos, colunas_de
from motor.estatistica import fmt_int, fmt_pct

LIMITE_TIMESTAMP_INVALIDO = 0.01
MIN_USUARIOS_AVISO = 1000


@dataclass
class ResultadoChecagem:
    arquivos: dict[str, Path]
    ok: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    bloqueios: list[str] = field(default_factory=list)
    lacunas: list[str] = field(default_factory=list)
    resumo: dict[str, Any] = field(default_factory=dict)
    palpite_status: str = "sem_palpite"  # especifico | vago | sem_palpite
    sugestoes_palpite: list[str] = field(default_factory=list)
    con: duckdb.DuckDBPyConnection | None = None

    @property
    def pode_seguir(self) -> bool:
        return not self.bloqueios

    @property
    def modo_comportamental(self) -> bool:
        return "perfil" not in self.arquivos


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.casefold()


def classificar_palpite(palpite: str | None, contexto: ContextoSquad, jornada_id: str) -> str:
    if not palpite or not palpite.strip():
        return "sem_palpite"
    alvo = _normalizar(palpite)
    termos = [t for lista in contexto.sinonimos().values() for t in lista]
    termos += contexto.eventos(jornada_id) + contexto.paginas(jornada_id)
    return "especifico" if any(_normalizar(t) in alvo for t in termos) else "vago"


def _checar_timestamps(con, tabela: str, arquivo: str, r: ResultadoChecagem) -> None:
    total, invalidos, exemplo = con.execute(
        f'SELECT count(*), count(*) FILTER (WHERE TRY_CAST("timestamp" AS TIMESTAMP) IS NULL), '
        f'any_value("timestamp") FILTER (WHERE TRY_CAST("timestamp" AS TIMESTAMP) IS NULL) FROM {tabela}'
    ).fetchone()
    if not total:
        r.bloqueios.append(f"{arquivo}.csv está vazio.")
        return
    fracao = invalidos / total
    if fracao > LIMITE_TIMESTAMP_INVALIDO:
        r.bloqueios.append(
            f"{fmt_int(invalidos)} de {fmt_int(total)} linhas ({fmt_pct(fracao)}) de {arquivo}.csv têm timestamp inválido "
            f"(ex.: “{exemplo}”). Formato esperado: AAAA-MM-DD HH:MM:SS. Corrija o arquivo e envie de novo."
        )
    elif invalidos:
        r.avisos.append(f"{fmt_int(invalidos)} linhas de {arquivo}.csv com timestamp inválido foram descartadas "
                        f"({fmt_pct(fracao, 2)} do arquivo).")


def checar_entrada(
    arquivos: dict[str, Path],
    contexto: ContextoSquad,
    jornada_id: str,
    inicio: date,
    fim: date,
    palpite: str | None,
) -> ResultadoChecagem:
    r = ResultadoChecagem(arquivos=dict(arquivos))
    r.palpite_status = classificar_palpite(palpite, contexto, jornada_id)
    if r.palpite_status == "vago":
        r.sugestoes_palpite = list(contexto.squad.get("exemplos_palpite", []))
        r.avisos.append("Palpite vago: não cita etapa, página, métrica nem grupo. Dá para refinar (veja sugestões) "
                        "ou seguir com exploração aberta, e o dossiê avisa.")

    if ARQUIVO_OBRIGATORIO not in arquivos:
        r.bloqueios.append("Falta tagueamento.csv, que é obrigatório: é dele que sai o funil da jornada.")
        return r
    if inicio > fim:
        r.bloqueios.append("A data inicial do período é depois da data final.")
        return r
    try:
        con = abrir_brutos(arquivos)
    except duckdb.Error as e:
        r.bloqueios.append(f"Não consegui ler os arquivos como CSV ({type(e).__name__}). Confira o formato e o separador.")
        return r
    r.con = con

    for nome in arquivos:
        colunas = [c.strip().lower() for c in colunas_de(con, f"raw_{nome}")]
        faltando = [c for c in COLUNAS_CSV[nome] if c not in colunas]
        if faltando:
            r.bloqueios.append(f"{nome}.csv sem as colunas obrigatórias: {', '.join(faltando)}.")
        if nome == "perfil":
            proibidas = [c for c in colunas if c in COLUNAS_PROIBIDAS_PERFIL]
            if proibidas:
                r.bloqueios.append(
                    f"perfil.csv traz colunas que identificam a pessoa ou são mais finas que faixas ({', '.join(proibidas)}). "
                    "Envie só faixas (faixa etária, segmento, tempo de conta), sem dados pessoais."
                )
    if r.bloqueios:
        return r

    for nome in arquivos:
        pii = con.execute(
            f"SELECT count(*) FROM raw_{nome} WHERE user_id_hash LIKE '%@%' OR regexp_full_match(trim(user_id_hash), '[0-9]{{11}}')"
        ).fetchone()[0]
        if pii:
            r.bloqueios.append(f"{nome}.csv tem {fmt_int(pii)} identificadores com cara de dado pessoal (e-mail ou CPF). "
                               "O user_id precisa chegar pseudonimizado (hash).")
    for nome in ("tagueamento", "fullstory"):
        if nome in arquivos:
            _checar_timestamps(con, f"raw_{nome}", nome, r)
    if r.bloqueios:
        return r

    minimo, maximo, no_periodo, usuarios = con.execute(
        'SELECT min(ts), max(ts), count(*) FILTER (WHERE ts >= ? AND ts < ?), '
        'count(DISTINCT user_id_hash) FILTER (WHERE ts >= ? AND ts < ?) '
        'FROM (SELECT TRY_CAST("timestamp" AS TIMESTAMP) AS ts, user_id_hash FROM raw_tagueamento)',
        [inicio, fim + timedelta(days=1)] * 2,
    ).fetchone()
    r.resumo.update({"dados_de": minimo, "dados_ate": maximo, "eventos_no_periodo": no_periodo, "usuarios_no_periodo": usuarios})
    if not no_periodo:
        r.bloqueios.append(f"Período vazio: nenhum evento entre {inicio:%d/%m/%Y} e {fim:%d/%m/%Y}. "
                           f"Os dados vão de {minimo:%d/%m/%Y} a {maximo:%d/%m/%Y}.")
        return r
    if usuarios < MIN_USUARIOS_AVISO:
        r.avisos.append(f"Só {fmt_int(usuarios)} usuários no período: poucos grupos devem ter n suficiente para evidência.")

    eventos_jornada = set(contexto.eventos(jornada_id))
    desconhecidos = con.execute(
        "SELECT event_name, count(*) FROM raw_tagueamento GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    fora = [e for e, _ in desconhecidos if e not in eventos_jornada]
    if fora:
        r.avisos.append(f"Eventos fora do dicionário da jornada serão ignorados: {', '.join(map(str, fora[:5]))}"
                        + ("…" if len(fora) > 5 else "") + ".")

    for nome in arquivos:
        linhas = con.execute(f"SELECT count(*) FROM raw_{nome}").fetchone()[0]
        r.resumo[f"linhas_{nome}"] = linhas
        r.ok.append(f"{nome}.csv: {fmt_int(linhas)} linhas, colunas conferidas.")
    r.ok.append(f"Período {inicio:%d/%m/%Y}–{fim:%d/%m/%Y}: {fmt_int(no_periodo)} eventos de {fmt_int(usuarios)} usuários "
                f"(os dados vão de {minimo:%d/%m/%Y} a {maximo:%d/%m/%Y}).")
    for nome, texto in LACUNA_POR_ARQUIVO.items():
        if nome not in arquivos:
            r.lacunas.append(texto)
    if "perfil" not in arquivos:
        r.avisos.append("Modo comportamental: sem perfil, os recortes por idade, segmento e tempo de conta ficam indisponíveis.")
    return r
