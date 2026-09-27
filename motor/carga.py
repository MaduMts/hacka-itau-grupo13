"""Carga dos arquivos no DuckDB e preparo da base analítica.

1. `abrir_brutos`: os CSVs entram como texto (tabelas raw_*). A checagem de entrada roda
   sobre elas, antes de qualquer conversão.
2. `preparar_base`: cria as tabelas tipadas e filtradas pelo período: `tag` (tagueamento),
   `usuarios` (metade A/B e dimensões), `funil_usuario` (etapas atingidas) e `fs` (FullStory).

A metade A/B é sorteada por usuário via md5(sal + id), calculada em Python: o hash() do
DuckDB não tem garantia de estabilidade entre versões, e a gravação da demo depende disso.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from config import SAL_HOLDOUT
from contexto.carregar import ContextoSquad
from contratos import COLUNAS_CSV, DIMENSOES_PERFIL

LIMITE_MEMORIA = "800MB"


@dataclass
class BaseAnalitica:
    con: duckdb.DuckDBPyConnection
    arquivos: dict[str, Path]
    jornada: dict[str, Any]
    inicio: date
    fim: date
    tem_perfil: bool
    tem_fullstory: bool
    tem_nps: bool
    dimensoes: list[str]
    valores: dict[str, list[str]]
    dados_hash: str
    n_usuarios: int
    n_eventos: int
    paginas_com_dados: list[str] = field(default_factory=list)

    @property
    def eventos_funil(self) -> list[str]:
        return list(self.jornada["eventos_funil"])

    @property
    def evento_erro(self) -> str | None:
        return self.jornada.get("evento_erro")

    @property
    def paginas(self) -> list[str]:
        return list(self.jornada["paginas"])

    def cursor(self) -> duckdb.DuckDBPyConnection:
        return self.con.cursor()


def localizar_arquivos(pasta: Path) -> dict[str, Path]:
    return {nome: pasta / f"{nome}.csv" for nome in COLUNAS_CSV if (pasta / f"{nome}.csv").exists()}


def hash_arquivos(arquivos: dict[str, Path]) -> str:
    h = hashlib.sha256()
    for nome in sorted(arquivos):
        h.update(nome.encode())
        with arquivos[nome].open("rb") as f:
            for bloco in iter(lambda: f.read(1 << 20), b""):
                h.update(bloco)
    return h.hexdigest()[:16]


def abrir_brutos(arquivos: dict[str, Path]) -> duckdb.DuckDBPyConnection:
    """Uma conexão DuckDB em memória com cada CSV como tabela de texto `raw_<nome>`."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{LIMITE_MEMORIA}'")
    for nome, caminho in arquivos.items():
        con.execute(
            f"CREATE TABLE raw_{nome} AS SELECT * FROM read_csv(?, header = true, all_varchar = true)",
            [caminho.as_posix()],
        )
    return con


def colunas_de(con: duckdb.DuckDBPyConnection, tabela: str) -> list[str]:
    return [linha[0] for linha in con.execute(f"DESCRIBE {tabela}").fetchall()]


def metade_de(user_id: str, sal: str = SAL_HOLDOUT) -> str:
    return "A" if hashlib.md5((sal + user_id).encode("utf-8")).digest()[0] % 2 == 0 else "B"


def preparar_base(
    con: duckdb.DuckDBPyConnection,
    arquivos: dict[str, Path],
    contexto: ContextoSquad,
    jornada_id: str,
    inicio: date,
    fim: date,
    sal: str = SAL_HOLDOUT,
) -> BaseAnalitica:
    jornada = contexto.jornada(jornada_id)
    tem_perfil, tem_fullstory, tem_nps = ("perfil" in arquivos, "fullstory" in arquivos, "nps" in arquivos)
    periodo = [inicio, fim + timedelta(days=1)]

    con.execute(
        """
        CREATE OR REPLACE TABLE tag AS
        SELECT trim(user_id_hash) AS user_id_hash, trim(session_id) AS session_id, ts, trim(event_name) AS event_name,
               lower(trim(platform)) AS platform, trim(app_version) AS app_version
        FROM (SELECT *, TRY_CAST("timestamp" AS TIMESTAMP) AS ts FROM raw_tagueamento)
        WHERE ts >= ? AND ts < ? AND coalesce(trim(user_id_hash), '') <> ''
        """,
        periodo,
    )

    ids = [linha[0] for linha in con.execute("SELECT DISTINCT user_id_hash FROM tag").fetchall()]
    con.register("df_metades", pd.DataFrame({"user_id_hash": ids, "metade": [metade_de(i, sal) for i in ids]}))
    con.execute("CREATE OR REPLACE TABLE metades AS SELECT * FROM df_metades")
    con.unregister("df_metades")

    col_perfil = ""
    join_perfil = ""
    if tem_perfil:
        col_perfil = "".join(f", coalesce(p.{d}, 'não informado') AS {d}" for d in DIMENSOES_PERFIL)
        join_perfil = (
            "LEFT JOIN (SELECT DISTINCT ON (trim(user_id_hash)) trim(user_id_hash) AS user_id_hash, "
            + ", ".join(f"trim({d}) AS {d}" for d in DIMENSOES_PERFIL)
            + " FROM raw_perfil) p USING (user_id_hash)"
        )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE usuarios AS
        WITH v AS (
            SELECT user_id_hash, arg_max(platform, ts) AS plataforma,
                   arg_max(platform || ' ' || app_version, ts) AS versao_app
            FROM tag GROUP BY 1
        )
        SELECT m.user_id_hash, m.metade, v.plataforma, v.versao_app{col_perfil}
        FROM metades m JOIN v USING (user_id_hash) {join_perfil}
        """
    )

    eventos = jornada["eventos_funil"] + ([jornada["evento_erro"]] if jornada.get("evento_erro") else [])
    colunas = [f"f{i}" for i in range(len(jornada["eventos_funil"]))] + (["erro"] if jornada.get("evento_erro") else [])
    flags = ", ".join(f"max(CASE WHEN event_name = ? THEN 1 ELSE 0 END)::INTEGER AS {c}" for c in colunas)
    con.execute(f"CREATE OR REPLACE TABLE funil_usuario AS SELECT user_id_hash, {flags} FROM tag GROUP BY 1", eventos)

    paginas_com_dados: list[str] = []
    if tem_fullstory:
        con.execute(
            """
            CREATE OR REPLACE TABLE fs AS
            SELECT trim(user_id_hash) AS user_id_hash, trim(session_id) AS session_id, ts, trim(page) AS page,
                   trim(event_type) AS event_type, coalesce(trim(element), '') AS element,
                   TRY_CAST(time_on_page_s AS DOUBLE) AS time_on_page_s
            FROM (SELECT *, TRY_CAST("timestamp" AS TIMESTAMP) AS ts FROM raw_fullstory)
            WHERE ts >= ? AND ts < ? AND trim(user_id_hash) IN (SELECT user_id_hash FROM usuarios)
            """,
            periodo,
        )
        paginas_com_dados = [l[0] for l in con.execute("SELECT DISTINCT page FROM fs ORDER BY 1").fetchall()]

    dimensoes = ["plataforma", "versao_app"] + (list(DIMENSOES_PERFIL) if tem_perfil else [])
    valores = {
        d: [l[0] for l in con.execute(f"SELECT DISTINCT CAST({d} AS VARCHAR) FROM usuarios ORDER BY 1").fetchall()]
        for d in dimensoes
    }
    n_usuarios = con.execute("SELECT count(*) FROM usuarios").fetchone()[0]
    n_eventos = con.execute("SELECT count(*) FROM tag").fetchone()[0]
    return BaseAnalitica(
        con=con, arquivos=arquivos, jornada=jornada, inicio=inicio, fim=fim,
        tem_perfil=tem_perfil, tem_fullstory=tem_fullstory, tem_nps=tem_nps,
        dimensoes=dimensoes, valores=valores, dados_hash=hash_arquivos(arquivos),
        n_usuarios=n_usuarios, n_eventos=n_eventos, paginas_com_dados=paginas_com_dados,
    )
