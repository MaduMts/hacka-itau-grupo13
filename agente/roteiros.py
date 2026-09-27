"""Roteiros para o ClienteRoteirizado: um "agente" determinístico, sem rede.

`roteiro_gabarito` pede as consultas certas para a jornada de exemplo e redige candidatas a
partir dos resultados reais, inclusive as armadilhas (segmento de maior volume, grupo pequeno),
para mostrar que são as regras do código que as rejeitam. Os textos não trazem números: os
números do dossiê vêm dos campos preenchidos pelo código.
"""
from __future__ import annotations

from typing import Any

from config import MAX_CANDIDATAS
from contratos import LinhaResultado, ResultadoConsulta
from motor.estatistica import destaque
from motor.registro_consultas import RegistroConsultas


def _pedido(ferramenta: str, motivo: str, **campos: Any) -> dict[str, Any]:
    base = {"ferramenta": ferramenta, "segmentar_por": "nenhum", "filtros": [], "pagina": None, "tipo_atrito": None,
            "evento_a": None, "evento_b": None, "janela_min": None, "motivo": motivo}
    base.update(campos)
    return base


def plano_gabarito(registro: RegistroConsultas) -> list[dict[str, Any]]:
    base = registro.base
    plano = [_pedido("funil", "Onde o funil perde mais gente, por versão do app", segmentar_por="versao_app")]
    if base.tem_fullstory:
        plano.append(_pedido("atrito", "Testar o palpite: a confirmação responde ao toque?", pagina="confirmacao_bloqueio",
                             tipo_atrito="dead_click", segmentar_por="versao_app"))
    if base.tem_perfil:
        plano.append(_pedido("funil", "Testar o palpite: abandono por faixa etária", segmentar_por="faixa_etaria"))
        if base.tem_fullstory:
            plano.append(_pedido("atrito", "Irritação na escolha do motivo por faixa etária", pagina="motivo_bloqueio",
                                 tipo_atrito="rage_click", segmentar_por="faixa_etaria"))
            plano.append(_pedido("atrito", "Testar o palpite: atrito na confirmação por faixa etária",
                                 pagina="confirmacao_bloqueio", tipo_atrito="dead_click", segmentar_por="faixa_etaria"))
        plano.append(_pedido("funil", "Conferir se o segmento de maior volume concentra o problema", segmentar_por="segmento"))
        if base.tem_fullstory:
            plano.append(_pedido("tempo_na_pagina", "Os mais velhos demoram mais na escolha do motivo?",
                                 pagina="motivo_bloqueio", segmentar_por="faixa_etaria"))
    plano.append(_pedido("sequencia", "Quantos desbloqueiam logo depois de bloquear?", evento_a="lock_success",
                         evento_b="unlock_success", janela_min=10,
                         segmentar_por="segmento" if base.tem_perfil else "plataforma"))
    if base.tem_nps and base.tem_perfil:
        plano.append(_pedido("temas_nps", "Pistas do porquê na voz do cliente, por faixa etária", segmentar_por="faixa_etaria"))
    return plano


def rotulo_metrica(metrica: str, res: ResultadoConsulta) -> str:
    tipo, _, alvo = metrica.partition(":")
    pagina = res.params.get("pagina")
    rotulos = {"dead_click": "dead click (toque que não responde)", "rage_click": "rage click (toques repetidos)",
               "lento": "tempo alto na tela", "sequencia": "sequência rápida"}
    if tipo == "abandono":
        return f"abandono antes de {alvo}"
    if tipo == "erro":
        return f"erro ({alvo})"
    texto = rotulos.get(tipo, metrica)
    return f"{texto} na página {pagina}" if pagina else texto


def _candidata(res: ResultadoConsulta, l: LinhaResultado) -> dict[str, Any]:
    descricao = rotulo_metrica(l.metrica, res)
    return {
        "tipo": "padrao_observado",
        "titulo": f"{descricao[0].upper()}{descricao[1:]} em {l.grupo}",
        "enunciado": f"No grupo {l.grupo}, a taxa de {descricao} é maior que nos demais usuários ({res.query_id}).",
        "evidencia_principal": {"query_id": res.query_id, "grupo": l.grupo, "metrica": l.metrica},
        "query_ids_citados": [res.query_id],
        "o_que_o_dado_responde": "Quantos usuários são afetados, em qual grupo e com que intensidade em relação aos demais.",
        "o_que_so_a_research_responde": "Por que isso acontece com esse grupo e o que o usuário tenta fazer nessa etapa.",
        "pergunta_para_research": f"O que os usuários de {l.grupo} vivem nessa etapa que os demais não vivem?",
    }


def redigir_gabarito(registro: RegistroConsultas, rodada: int) -> dict[str, Any]:
    exploracao = [r for r in registro.exploracao() if r.query_id != "Q00-A"]
    escolhidas: dict[tuple, tuple[ResultadoConsulta, LinhaResultado]] = {}
    for res in exploracao:
        for l in res.linhas:
            chave = (res.params.get("segmentar_por", ""), l.grupo, res.fonte == "nps")  # NPS separado: é só pista
            if destaque(l) and (chave not in escolhidas or (l.lift or 0) > (escolhidas[chave][1].lift or 0)):
                escolhidas[chave] = (res, l)
    candidatas = [_candidata(res, l) for (_, _, nps), (res, l) in escolhidas.items() if not nps]
    pistas_nps = [_candidata(res, l) for (_, _, nps), (res, l) in escolhidas.items() if nps]

    # Comportamento sem contraste (P3): o dado diz quantos; o porquê é pergunta para a Research.
    for res in exploracao:
        if res.ferramenta == "sequencia":
            candidatas.append({
                "tipo": "padrao_observado",
                "titulo": f"Parte dos usuários faz {res.params['evento_b']} logo depois de {res.params['evento_a']}",
                "enunciado": (f"Uma parte dos usuários faz {res.params['evento_b']} poucos minutos depois de "
                              f"{res.params['evento_a']}, em proporção parecida em todos os grupos ({res.query_id})."),
                "evidencia_principal": {"query_id": res.query_id, "grupo": None, "metrica": "sequencia"},
                "query_ids_citados": [res.query_id],
                "o_que_o_dado_responde": "Quantos usuários desfazem a ação em poucos minutos e quanto tempo levam.",
                "o_que_so_a_research_responde": ("Por que desfazem tão rápido: engano, uso do bloqueio como pausa "
                                                  "ou cartão encontrado logo em seguida?"),
                "pergunta_para_research": "O que leva o cliente a desbloquear o cartão poucos minutos depois de bloquear?",
            })
            break

    # Armadilhas que um agente real poderia propor: o código precisa rejeitá-las.
    for res in exploracao:
        if res.params.get("segmentar_por") == "segmento":
            maior = max((l for l in res.linhas if not l.suprimido and l.grupo != "todos"), key=lambda l: (l.n, l.casos),
                        default=None)
            if maior:
                candidatas.append(_candidata(res, maior))
            break
    pequenas = [(res, l) for res in exploracao for l in res.linhas
                if not l.suprimido and not destaque(l) and (l.lift or 0) >= 1.5 and l.ic95 and l.ic95[0] > 1]
    if pequenas:
        candidatas.append(_candidata(*max(pequenas, key=lambda par: par[1].lift or 0)))

    candidatas += pistas_nps[:1]  # NPS por último: é pista do porquê, nunca evidência
    destaques = [f"{l.grupo} em {rotulo_metrica(l.metrica, res)} ({res.query_id})"
                 for (_, _, nps), (res, l) in escolhidas.items() if not nps]
    resposta = ("Nos dados, os destaques são: " + "; ".join(destaques) + ".") if destaques else "Nenhum destaque claro nos dados."
    return {"rodada": rodada, "fase": "candidatas", "consultas": [], "candidatas": candidatas[:MAX_CANDIDATAS],
            "resposta_ao_palpite": resposta, "lacunas": []}


def roteiro_gabarito(rodada: int, textos: list[str], registro: RegistroConsultas) -> dict[str, Any]:
    if rodada == 1:
        return {"rodada": 1, "fase": "plano", "consultas": plano_gabarito(registro), "candidatas": [],
                "resposta_ao_palpite": None, "lacunas": []}
    return redigir_gabarito(registro, rodada)
