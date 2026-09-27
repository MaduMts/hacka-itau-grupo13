"""Cliente da API v1 do Devin (sessões).

Todo acesso ao Devin passa por aqui, então migrar para a v3 é uma troca local.
A chave nunca aparece em mensagens de erro, logs ou no registro.

Uma sessão pronta fica `blocked` (esperando a pessoa), não `finished`. Por isso
`aguardar_rodada` espera o status parar **e** o structured_output trazer a rodada pedida.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from config import DEVIN_API_BASE, DEVIN_INTERVALO_S, DEVIN_TIMEOUT_RODADA_S

STATUS_PRONTO = frozenset({"blocked", "finished"})
STATUS_FALHA = frozenset({"expired", "suspend_requested", "suspend_requested_frontend"})


class ErroDevin(Exception):
    """Falha ao falar com o Devin. `tipo` guia a mensagem na tela e o registro."""

    def __init__(self, tipo: str, mensagem: str, status_http: int | None = None):
        super().__init__(mensagem)
        self.tipo = tipo
        self.status_http = status_http


@dataclass
class EstadoSessao:
    session_id: str
    status_enum: str | None
    status: str | None
    structured_output: dict[str, Any] | None
    mensagens: list[dict[str, Any]]
    bruto: dict[str, Any]

    @property
    def rodada(self) -> int | None:
        valor = (self.structured_output or {}).get("rodada")
        return valor if isinstance(valor, int) else None


def _erro_http(status: int, metodo: str, caminho: str, trecho: str) -> ErroDevin:
    tipos = {
        401: ("autenticacao", "Chave do Devin inválida ou expirada"),
        402: ("cota", "Cota do Devin esgotada para esta chave"),
        403: ("permissao", "A chave do Devin não tem permissão para esta operação"),
        404: ("nao_encontrado", "Recurso não encontrado no Devin"),
        429: ("limite", "Limite de requisições ou de uso do Devin atingido; aguarde ou troque a chave"),
    }
    tipo, texto = tipos.get(status, ("servidor" if status >= 500 else "requisicao", "Erro na API do Devin"))
    detalhe = f" Resposta: {trecho}" if trecho and tipo in ("requisicao", "servidor") else ""
    return ErroDevin(tipo, f"{texto} (HTTP {status} em {metodo} {caminho}).{detalhe}", status)


class ClienteDevin:
    def __init__(
        self,
        chave: str | None,
        base: str = DEVIN_API_BASE,
        timeout_http: float = 30.0,
        http: Any = None,
        dormir: Callable[[float], None] = time.sleep,
        relogio: Callable[[], float] = time.monotonic,
    ):
        if not chave:
            raise ErroDevin("config", "DEVIN_API_KEY ausente: configure no .env ou nos Secrets do Streamlit.")
        self._base = base.rstrip("/")
        self._timeout = timeout_http
        self._http = http or requests.Session()
        self._headers = {"Authorization": f"Bearer {chave}", "Content-Type": "application/json"}
        self._dormir = dormir
        self._relogio = relogio

    def _req(self, metodo: str, caminho: str, **kwargs: Any) -> Any:
        try:
            r = self._http.request(metodo, f"{self._base}{caminho}", headers=self._headers, timeout=self._timeout, **kwargs)
        except requests.Timeout:
            raise ErroDevin("timeout", f"Tempo esgotado falando com o Devin ({metodo} {caminho}).") from None
        except requests.RequestException as e:
            raise ErroDevin("rede", f"Falha de rede falando com o Devin ({type(e).__name__}).") from None
        if r.status_code >= 400:
            raise _erro_http(r.status_code, metodo, caminho, (r.text or "")[:200])
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    # --- Operações da API v1 ---

    def listar_sessoes(self, limite: int = 1) -> list[dict[str, Any]]:
        dados = self._req("GET", "/sessions", params={"limit": limite})
        if isinstance(dados, dict):
            return list(dados.get("sessions") or [])
        return list(dados or [])

    def criar_sessao(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        titulo: str | None = None,
        tags: list[str] | None = None,
        max_acu: int | None = None,
    ) -> tuple[str, str]:
        corpo: dict[str, Any] = {
            "prompt": prompt,
            "idempotent": False,
            "unlisted": True,
            "knowledge_ids": [],  # nada do conhecimento da organização entra na sessão
            "secret_ids": [],  # nenhum segredo da organização fica disponível ao agente
        }
        if schema is not None:
            corpo["structured_output_schema"] = schema
        if titulo:
            corpo["title"] = titulo
        if tags:
            corpo["tags"] = tags
        if max_acu:
            corpo["max_acu_limit"] = max_acu
        dados = self._req("POST", "/sessions", json=corpo)
        if not isinstance(dados, dict) or "session_id" not in dados:
            raise ErroDevin("formato", "Resposta inesperada ao criar sessão no Devin.")
        return dados["session_id"], dados.get("url") or ""

    def estado(self, session_id: str) -> EstadoSessao:
        dados = self._req("GET", f"/sessions/{session_id}")
        if not isinstance(dados, dict):
            raise ErroDevin("formato", "Resposta inesperada ao consultar a sessão no Devin.")
        so = dados.get("structured_output")
        if isinstance(so, str):
            try:
                so = json.loads(so)
            except ValueError:
                so = None
        return EstadoSessao(
            session_id=session_id,
            status_enum=dados.get("status_enum"),
            status=dados.get("status"),
            structured_output=so if isinstance(so, dict) else None,
            mensagens=list(dados.get("messages") or []),
            bruto=dados,
        )

    def enviar_mensagem(self, session_id: str, mensagem: str) -> None:
        self._req("POST", f"/sessions/{session_id}/message", json={"message": mensagem})

    def encerrar(self, session_id: str) -> None:
        try:
            self._req("DELETE", f"/sessions/{session_id}")
        except ErroDevin as e:
            if e.tipo != "nao_encontrado":
                raise

    # --- Espera por rodada ---

    def aguardar_rodada(
        self,
        session_id: str,
        rodada: int,
        *,
        timeout_s: float = DEVIN_TIMEOUT_RODADA_S,
        intervalo_s: float = DEVIN_INTERVALO_S,
        carencia_s: float = 60.0,
        cutucada: str | None = None,
        ao_consultar: Callable[[EstadoSessao, float], None] | None = None,
    ) -> EstadoSessao:
        """Espera o agente publicar o structured_output da `rodada` e parar.

        Se ele parar sem publicar depois de trabalhar (ou não acordar dentro da carência),
        manda uma única `cutucada`. Se parar de novo sem publicar, falha com tipo "formato".
        """
        inicio = marco = self._relogio()
        viu_trabalhando = cutucou = False
        parado = 0
        while True:
            est = self.estado(session_id)
            agora = self._relogio()
            if ao_consultar:
                ao_consultar(est, agora - inicio)
            if est.status_enum in STATUS_FALHA:
                raise ErroDevin("sessao_encerrada", f"A sessão do Devin parou com status '{est.status_enum}' antes da rodada {rodada}.")
            if est.status_enum in STATUS_PRONTO and est.rodada == rodada:
                return est
            if est.status_enum in STATUS_PRONTO:
                parado = parado + 1 if viu_trabalhando else parado
                travado = (viu_trabalhando and parado >= 2) or (not viu_trabalhando and agora - marco >= carencia_s)
                if travado and cutucada and not cutucou:
                    self.enviar_mensagem(session_id, cutucada)
                    cutucou, viu_trabalhando, parado, marco = True, False, 0, agora
                elif travado:
                    raise ErroDevin("formato", f"O Devin parou sem publicar o structured_output da rodada {rodada}.")
            else:
                viu_trabalhando, parado = True, 0
            if agora - inicio >= timeout_s:
                raise ErroDevin("timeout", f"O Devin não concluiu a rodada {rodada} em {timeout_s:.0f} s.")
            self._dormir(intervalo_s)
