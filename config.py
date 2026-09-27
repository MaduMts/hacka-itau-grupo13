"""Configuração central do MVP.

Limiares estatísticos, orçamento do agente e parâmetros do Devin ficam aqui para que
motor, regras de rótulo, UI e testes usem a mesma fonte. Credenciais vêm só do ambiente
(.env local) ou dos Secrets do Streamlit, nunca do código, e nunca são registradas.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")

# --- Privacidade e estatística (ver README e plano) ---
N_MIN_EXIBICAO = 50  # grupos menores nunca são exibidos nem enviados ao agente
N_MIN_EVIDENCIA = 300  # grupo e resto, em cada metade
CASOS_MIN = 20  # usuários afetados no grupo, em cada metade
LIFT_MIN_A = 1.5  # efeito mínimo na metade A para não ser descartado
LIFT_MIN_B = 1.25  # réplica na metade B (mais frouxa: absorve a "maldição do vencedor")
Z_95 = 1.959964
Z_99 = 2.575829
MAX_FILTROS = 1  # segmentar_por + 1 filtro = no máximo 2 dimensões por tabela
FATOR_FULLSTORY = 10.0  # FullStory é amostra de 10% das sessões

# --- Agente ---
ORCAMENTO_CONSULTAS = 10
MAX_CANDIDATAS = 5
MODO_EXPLORACAO = os.getenv("MODO_EXPLORACAO", "rodadas")  # "rodadas" | "varredura"
PROMPT_VERSAO = "v0.1"

# --- Devin (API v1) ---
DEVIN_API_BASE = os.getenv("DEVIN_API_BASE", "https://api.devin.ai/v1").rstrip("/")
DEVIN_MAX_ACU = int(os.getenv("DEVIN_MAX_ACU", "2"))
DEVIN_INTERVALO_S = float(os.getenv("DEVIN_INTERVALO_S", "3"))
DEVIN_TIMEOUT_RODADA_S = float(os.getenv("DEVIN_TIMEOUT_RODADA_S", "180"))

# --- Dados e registros ---
SEMENTE = 20260801
SAL_HOLDOUT = "g13-holdout-v1"
PASTA_DADOS = RAIZ / "data" / "gerados"
PASTA_REGISTROS = Path(os.getenv("PASTA_REGISTROS", str(RAIZ / "registros")))
PASTA_CONTEXTO = RAIZ / "contexto"
SQUAD_PADRAO = os.getenv("SQUAD_PADRAO", "cartoes")


def chave_devin() -> str | None:
    """Chave do Devin do ambiente ou dos Secrets do Streamlit. Nunca registrar o valor."""
    chave = os.getenv("DEVIN_API_KEY", "").strip()
    if chave:
        return chave
    try:
        import streamlit as st

        valor = st.secrets.get("DEVIN_API_KEY")
    except Exception:
        return None
    if not valor:
        return None
    return str(valor).strip() or None
