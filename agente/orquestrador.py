"""Rodadas com o agente: R1 planejar → código executa na metade A → R2 redigir → R3 corrigir.

O agente nunca executa nada: ele publica um plano no structured output, nosso código roda as
consultas do catálogo e devolve só tabelas agregadas por mensagem.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agente.cliente_devin import ErroDevin
from agente.prompts import cutucada, mensagem_correcao, mensagem_rodada_2
from agente.protocolo import ErroProtocolo, validar_saida
from config import DEVIN_MAX_ACU, ORCAMENTO_CONSULTAS
from contratos import ResultadoConsulta, SaidaDevin, schema_devin
from motor.consultas import ErroConsulta, descrever_params
from motor.registro_consultas import RegistroConsultas
from registro import Registro

Progresso = Callable[[str, str], None]
_STATUS = {"working": "trabalhando", "blocked": "terminou a rodada", "finished": "terminou", "resumed": "retomou"}


@dataclass
class ResultadoAgente:
    plano: SaidaDevin | None = None
    candidatas: SaidaDevin | None = None
    executadas: list[str] = field(default_factory=list)
    erros_consulta: list[str] = field(default_factory=list)
    session_id: str | None = None
    url: str | None = None
    textos_enviados: list[dict[str, Any]] = field(default_factory=list)
    duracoes: dict[int, float] = field(default_factory=dict)


class Orquestrador:
    def __init__(self, cliente: Any, consultas: RegistroConsultas, registro: Registro,
                 ao_progresso: Progresso | None = None, orcamento: int = ORCAMENTO_CONSULTAS):
        self.cliente = cliente
        self.consultas = consultas
        self.registro = registro
        self.ao_progresso = ao_progresso
        self.orcamento = orcamento
        self.resultado = ResultadoAgente()
        if hasattr(cliente, "registro"):
            cliente.registro = consultas  # o cliente roteirizado redige a partir dos resultados reais

    def _avisar(self, etapa: str, mensagem: str) -> None:
        if self.ao_progresso:
            self.ao_progresso(etapa, mensagem)

    def _enviar(self, rodada: int, texto: str) -> None:
        self.cliente.enviar_mensagem(self.resultado.session_id, texto)
        self.resultado.textos_enviados.append({"rodada": rodada, "tipo": "mensagem", "texto": texto})

    def _esperar(self, rodada: int, fase: str) -> SaidaDevin:
        inicio = time.monotonic()

        def ao_consultar(est, decorrido: float) -> None:
            self._avisar("agente", f"Rodada {rodada}: agente {_STATUS.get(est.status_enum, est.status_enum)} · {decorrido:.0f} s")

        est = self.cliente.aguardar_rodada(self.resultado.session_id, rodada, cutucada=cutucada(rodada), ao_consultar=ao_consultar)
        try:
            saida = validar_saida(est.structured_output, rodada, fase)
        except ErroProtocolo as e:
            self.registro.evento("sistema", "formato_invalido", rodada=rodada, detalhe=str(e))
            self._enviar(rodada, f"O structured output da rodada {rodada} veio fora do formato ({e}). "
                                 f"Publique de novo com rodada={rodada}, seguindo o schema, e aguarde.")
            est = self.cliente.aguardar_rodada(self.resultado.session_id, rodada, diferente_de=est.structured_output,
                                               ao_consultar=ao_consultar)
            saida = validar_saida(est.structured_output, rodada, fase)
        self.resultado.duracoes[rodada] = round(time.monotonic() - inicio, 1)
        self.registro.evento("agente", "rodada_publicada", rodada=rodada, fase=fase,
                             segundos=self.resultado.duracoes[rodada], structured_output=saida.model_dump())
        return saida

    def rodada_1(self, prompt: str, titulo: str, tags: list[str]) -> SaidaDevin:
        self._avisar("agente", "Abrindo sessão com o agente")
        sid, url = self.cliente.criar_sessao(prompt, schema=schema_devin(), titulo=titulo, tags=tags, max_acu=DEVIN_MAX_ACU)
        self.resultado.session_id, self.resultado.url = sid, url
        self.resultado.textos_enviados.append({"rodada": 1, "tipo": "prompt", "texto": prompt})
        self.registro.evento("sistema", "sessao_criada", session_id=sid, url=url)
        if url:
            self._avisar("sessao", url)
        self.resultado.plano = self._esperar(1, "plano")
        return self.resultado.plano

    def executar_plano(self, plano: SaidaDevin) -> list[ResultadoConsulta]:
        pedidos = plano.consultas[: self.orcamento]
        if len(plano.consultas) > self.orcamento:
            excesso = len(plano.consultas) - self.orcamento
            self.resultado.erros_consulta.append(f"{excesso} consulta(s) além do orçamento de {self.orcamento} foram ignoradas.")
        resultados = []
        for i, pedido in enumerate(pedidos, 1):
            try:
                res = self.consultas.executar(pedido, restantes=self.orcamento - i)
            except ErroConsulta as e:
                self.resultado.erros_consulta.append(f"consulta {i} ({pedido.ferramenta}): {e}")
                self.registro.evento("codigo", "consulta_invalida", ordem=i, pedido=pedido.model_dump(), erro=str(e))
                self._avisar("consulta", f"Consulta {i} inválida: {e}")
                continue
            resultados.append(res)
            self.resultado.executadas.append(res.query_id)
            self.registro.evento("codigo", "consulta_executada", query_id=res.query_id, params=res.params,
                                 motivo=pedido.motivo, duracao_ms=res.duracao_ms)
            self._avisar("consulta", f"{res.query_id} · {res.ferramenta} · {descrever_params(res.params)} — {pedido.motivo}")
        return resultados

    def rodada_2(self, resultados: list[ResultadoConsulta]) -> SaidaDevin:
        self._enviar(2, mensagem_rodada_2(resultados, self.resultado.erros_consulta))
        self.resultado.candidatas = self._esperar(2, "candidatas")
        return self.resultado.candidatas

    def corrigir(self, problemas: list[str], rodada: int = 3) -> SaidaDevin:
        self._enviar(rodada, mensagem_correcao(rodada, problemas))
        self.resultado.candidatas = self._esperar(rodada, "candidatas")
        return self.resultado.candidatas

    def encerrar(self) -> None:
        if not self.resultado.session_id:
            return
        try:
            self.cliente.encerrar(self.resultado.session_id)
            self.registro.evento("sistema", "sessao_encerrada", session_id=self.resultado.session_id)
        except ErroDevin as e:
            self.registro.evento("sistema", "aviso", detalhe=f"Não consegui encerrar a sessão do agente ({e.tipo}).")
