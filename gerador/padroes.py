"""Parâmetros dos dados sintéticos e GABARITO dos padrões plantados.

Fonte única: o gerador, os testes, o conferidor de gabarito e a UI leem daqui.
Tudo é fictício. Os atributos dos usuários são sorteados de forma independente,
então não há confusão entre dimensões (ex.: idade não depende de versão do app).
"""
from __future__ import annotations

from datetime import datetime

N_USUARIOS = 200_000
INICIO = datetime(2026, 8, 1, 0, 0, 0)
FIM = datetime(2026, 8, 30, 23, 59, 59)
DIAS = 30

# Ilustra a prática recomendada (HMAC com segredo). Em produção o segredo fica no cofre do banco.
SEGREDO_HMAC_EXEMPLO = b"segredo-de-exemplo-hackathon-g13-nao-usar-em-producao"

# --- Usuários ---
PLATAFORMAS = {"android": 0.58, "ios": 0.42}
VERSOES = {
    "android": {"8.2.1": 0.14, "8.3.0": 0.30, "8.4.0": 0.18, "8.5.0": 0.3786, "8.6.0-beta": 0.0014},
    "ios": {"8.3.0": 0.22, "8.4.1": 0.33, "8.5.0": 0.45},
}
FAIXAS_ETARIAS = {"18-24": 0.14, "25-34": 0.27, "35-44": 0.24, "45-59": 0.23, "60+": 0.12}
SEGMENTOS = {"Aurora": 0.46, "Boreal": 0.27, "Cerrado": 0.17, "Delta": 0.10}
TEMPO_DE_CONTA = {"<6m": 0.10, "6-24m": 0.22, "2-5a": 0.33, "5a+": 0.35}  # só ruído

# Peso de cada hora do dia no início das sessões (mais uso de dia)
PESO_HORA = [1, 1, 1, 1, 1, 2, 4, 6, 8, 9, 9, 9, 10, 9, 9, 9, 9, 9, 9, 8, 7, 5, 3, 2]

# --- Sessões e funil ---
P_SEGUNDA_SESSAO = 0.05  # além da sessão de bloqueio que todo usuário tem no período
P_SO_DESBLOQUEIO = 0.05  # desbloqueio de um cartão bloqueado antes do período
FUNIL_BASE = {"card_select": 0.95, "lock_reason_select": 0.93, "lock_confirm": 0.95}
P_ERRO_BASE = 0.035
P_SUCESSO_DESBLOQUEIO = 0.97
DESBLOQUEIO_APOS_TOQUE_S = (3, 15)  # unlock_start → unlock_success

# --- Padrões plantados ---
P1 = {  # dead click na confirmação numa versão específica do Android → Evidência
    "plataforma": "android", "versao": "8.4.0",
    "p_confirm": 0.80, "p_dead_confirmacao": 0.35,
    "elemento": "btn_confirmar_bloqueio", "p_elemento": 0.90, "mediana_confirmacao_s": 9.0,
}
P2 = {  # 60+ demoram e dão rage click na escolha do motivo → Evidência
    "faixa": "60+", "p_reason": 0.84, "p_rage_motivo": 0.12,
    "elemento": "lista_motivos", "p_elemento": 0.80, "mediana_motivo_s": 27.0, "sigma_motivo": 0.50,
}
P3 = {  # bloqueia e desbloqueia em menos de 10 min, igual em todos os grupos → Hipótese
    "p_rapido": 0.14, "media_min": 3.0, "limite_min": 9.5,
    "p_tardio": 0.30, "tardio_min_s": 2 * 3600, "tardio_max_s": 12 * 86400,
}
T4 = {"segmento": "Aurora"}  # armadilha de taxa-base: maior volume, mesma taxa (sem efeito plantado)
T5 = {"plataforma": "android", "versao": "8.6.0-beta", "p_erro": 0.45}  # armadilha de amostra pequena

# --- FullStory (amostra de sessões) ---
FULLSTORY_AMOSTRA = 0.10
P_DEAD_BASE = 0.02
P_DEAD_CONFIRMACAO_BASE = 0.03
P_RAGE_BASE = 0.015
P_RAGE_MOTIVO_BASE = 0.025
MEDIANAS_S = {
    "selecao_cartao": 6.0, "motivo_bloqueio": 11.0, "confirmacao_bloqueio": 5.0,
    "resultado_bloqueio": 3.0, "desbloqueio": 5.0,
}
SIGMA_TEMPO = 0.45
FATOR_TEMPO_ABANDONO = 1.6  # quem desiste na página fica mais tempo nela
PROCESSAMENTO_S = (1.0, 3.0)  # lock_confirm → lock_success/lock_error
ELEMENTOS = {
    "selecao_cartao": ["item_cartao", "btn_voltar"],
    "motivo_bloqueio": ["lista_motivos", "opcao_motivo_perda", "opcao_motivo_roubo",
                        "opcao_motivo_temporario", "opcao_motivo_outro", "btn_continuar"],
    "confirmacao_bloqueio": ["btn_confirmar_bloqueio", "btn_cancelar"],
    "resultado_bloqueio": ["btn_concluir", "link_desbloquear"],
    "desbloqueio": ["btn_desbloquear"],
}
CLIQUE_AO_AVANCAR = {
    "selecao_cartao": "item_cartao", "motivo_bloqueio": "btn_continuar",
    "confirmacao_bloqueio": "btn_confirmar_bloqueio", "resultado_bloqueio": "btn_concluir",
    "desbloqueio": "btn_desbloquear",
}

# --- GABARITO: o que o agente deve achar e o que deve ignorar ---
GABARITO = [
    {"id": "P1", "descricao": "Dead click na confirmação no Android 8.4.0",
     "dimensao": "versao_app", "grupo": "android 8.4.0", "metricas": ["dead_click", "abandono:lock_confirm"],
     "esperado": "Evidência"},
    {"id": "P2", "descricao": "60+ travam na escolha do motivo (rage click, tempo, abandono)",
     "dimensao": "faixa_etaria", "grupo": "60+", "metricas": ["rage_click", "lento", "abandono:lock_reason_select"],
     "esperado": "Evidência"},
    {"id": "P3", "descricao": "Bloqueiam e desbloqueiam em menos de 10 min",
     "dimensao": None, "grupo": None, "metricas": ["sequencia"], "esperado": "Hipótese"},
    {"id": "T4", "descricao": "Armadilha de taxa-base: segmento Aurora (maior volume, lift ≈ 1)",
     "dimensao": "segmento", "grupo": "Aurora", "metricas": [], "esperado": "não promovido"},
    {"id": "T5", "descricao": "Armadilha de amostra pequena: Android 8.6.0-beta",
     "dimensao": "versao_app", "grupo": "android 8.6.0-beta", "metricas": ["erro:lock_error"],
     "esperado": "nunca Evidência"},
]
