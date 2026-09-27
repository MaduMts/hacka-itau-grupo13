"""Registro de consultas da execução: emite os `query_id`s e guarda cada resultado.

`Q03-A` é a consulta pedida pelo agente (metade A). `replicar("Q03-A", "B")` re-executa
exatamente a mesma especificação na metade B (`Q03-B`) ou na base toda (`Q03-T`).
Numeração na ordem do plano, então o replay da demo reproduz os mesmos ids.
"""
from __future__ import annotations

from contratos import LinhaResultado, PedidoConsulta, ResultadoConsulta
from motor import consultas
from motor.carga import BaseAnalitica
from motor.visao_geral import panorama


class RegistroConsultas:
    def __init__(self, base: BaseAnalitica):
        self.base = base
        self.resultados: dict[str, ResultadoConsulta] = {}
        self.pedidos: dict[str, PedidoConsulta] = {}
        self._seq = 0

    def panorama(self) -> ResultadoConsulta:
        res = panorama(self.base, "A", "Q00-A")
        self.resultados[res.query_id] = res
        return res

    def executar(self, pedido: PedidoConsulta, restantes: int | None = None) -> ResultadoConsulta:
        """Valida e executa na metade A. Pedido inválido levanta ErroConsulta (sem consumir id)."""
        consultas.validar(self.base, pedido)
        self._seq += 1
        qid = f"Q{self._seq:02d}-A"
        res = consultas.executar(self.base, pedido, "A", qid)
        res.texto_llm = consultas.texto_para_agente(res, restantes)
        self.resultados[qid] = res
        self.pedidos[qid] = pedido
        return res

    def replicar(self, query_id: str, metade: str) -> ResultadoConsulta:
        novo = f"{query_id.rsplit('-', 1)[0]}-{metade}"
        if novo in self.resultados:
            return self.resultados[novo]
        if query_id == "Q00-A":
            res = panorama(self.base, metade, novo)
        else:
            res = consultas.executar(self.base, self.pedidos[query_id], metade, novo)
            res.texto_llm = consultas.texto_para_agente(res)
        self.resultados[novo] = res
        return res

    def obter(self, query_id: str) -> ResultadoConsulta | None:
        return self.resultados.get(query_id)

    def linha(self, query_id: str, grupo: str | None, metrica: str) -> LinhaResultado | None:
        res = self.obter(query_id)
        if res is None:
            return None
        alvo = grupo if grupo is not None else "todos"
        return next((l for l in res.linhas if l.grupo == alvo and l.metrica == metrica), None)

    def exploracao(self) -> list[ResultadoConsulta]:
        return [r for qid, r in self.resultados.items() if qid.endswith("-A")]
