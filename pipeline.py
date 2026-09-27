"""Uma execução de ponta a ponta.

checagem (já feita) → base analítica → panorama Q00-A → R1 plano → consultas na metade A
→ R2 candidatas → verificador (+1 correção, R3) → refutação na metade B → rótulos → dossiê.
Tudo vai para o registro append-only da execução.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agente.cliente_devin import ErroDevin
from agente.orquestrador import Orquestrador, ResultadoAgente
from agente.prompts import prompt_rodada_1, prompt_sha
from agente.protocolo import ErroProtocolo, descrever_catalogo
from config import FATOR_FULLSTORY, MAX_CANDIDATAS, PROMPT_VERSAO, SEMENTE
from contexto.carregar import ContextoSquad
from contexto.memoria import decisoes_recentes, texto_memoria
from contratos import Candidata, Dossie, EntradaPM, MetaExecucao, Verificacao
from entrada.checagem import ResultadoChecagem
from motor.carga import preparar_base
from motor.registro_consultas import RegistroConsultas
from registro import Registro, agora_iso, novo_run_id
from verificacao.numeros import marcar_sem_fonte, problemas_para_agente, verificar_candidata, verificar_textos_gerais
from verificacao.refutacao import refutar
from verificacao.rotulos import deduplicar, montar_hipotese, ranquear

Progresso = Callable[[str, str], None]


@dataclass
class Execucao:
    run_id: str
    entrada: EntradaPM
    registro: Registro
    dossie: Dossie | None = None
    consultas: RegistroConsultas | None = None
    agente: ResultadoAgente | None = None
    verificacao_geral: Verificacao | None = None
    erro: str | None = None
    erro_tipo: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def avisos_padrao(consultas: RegistroConsultas, llm: str, dados_sinteticos: bool) -> list[str]:
    base = consultas.base
    avisos = []
    if dados_sinteticos:
        avisos.append("Dados sintéticos: exercício fictício de hackathon, nenhum dado real de cliente.")
    avisos += [
        "Correlação não é causa: as explicações são hipóteses para a Research ou para experimento.",
        "Grupos com menos de 50 usuários foram suprimidos.",
    ]
    if base.tem_fullstory:
        avisos.append(f"FullStory é amostra de 10% das sessões: contagens de atrito são estimativas (×{FATOR_FULLSTORY:.0f}).")
    if not base.tem_perfil:
        avisos.append("Modo comportamental: sem perfil, não há recortes por idade, segmento ou tempo de conta.")
    avisos += [
        "Não use estes resultados para decisões sobre clientes individuais, ofertas, crédito ou restrição de acesso.",
        "Recortes por idade servem para remover barreiras, nunca para restringir acesso.",
    ]
    if llm == "devin-v1":
        avisos.append("Só tabelas agregadas foram enviadas ao agente externo (Devin, da Cognition, processado nos EUA).")
    else:
        avisos.append("Execução com agente roteirizado ou gravado: nada foi enviado a fornecedor externo.")
    return avisos


def _verificar(saida, consultas: RegistroConsultas) -> tuple[list[Verificacao], Verificacao]:
    candidatas = saida.candidatas[:MAX_CANDIDATAS]
    return [verificar_candidata(c, consultas) for c in candidatas], verificar_textos_gerais(saida, consultas)


def _marcar(c: Candidata, v: Verificacao) -> Candidata:
    if not v.orfaos:
        return c
    campos = ("titulo", "enunciado", "o_que_o_dado_responde", "o_que_so_a_research_responde", "pergunta_para_research")
    return c.model_copy(update={campo: marcar_sem_fonte(getattr(c, campo), v.orfaos) for campo in campos})


def rodar_execucao(
    entrada: EntradaPM,
    checagem: ResultadoChecagem,
    contexto: ContextoSquad,
    cliente: Any,
    ao_progresso: Progresso | None = None,
    parent_run_id: str | None = None,
    dados_sinteticos: bool = True,
) -> Execucao:
    run_id = novo_run_id()
    reg = Registro(run_id)
    exe = Execucao(run_id=run_id, entrada=entrada, registro=reg)
    avisar = ao_progresso or (lambda etapa, msg: None)
    empresa = contexto.empresa.get("politicas_dados", {})
    reg.evento("sistema", "execucao_iniciada", entrada=entrada.model_dump(mode="json"), parent_run_id=parent_run_id,
               squad=contexto.id, contexto_sha=contexto.sha, prompt_versao=PROMPT_VERSAO, prompt_sha=prompt_sha(),
               llm=getattr(cliente, "llm", "desconhecido"), lia_id=empresa.get("lia_id"), ripd_id=empresa.get("ripd_id"),
               donos_das_fontes={nome: contexto.dono_da_fonte(nome) for nome in checagem.arquivos})
    reg.evento("codigo", "entrada_checada", ok=checagem.ok, avisos=checagem.avisos, lacunas=checagem.lacunas,
               palpite_status=checagem.palpite_status)
    if not checagem.pode_seguir or checagem.con is None:
        exe.erro, exe.erro_tipo = "A checagem da entrada bloqueou a execução.", "entrada"
        reg.evento("sistema", "execucao_bloqueada", bloqueios=checagem.bloqueios)
        return exe

    orq: Orquestrador | None = None
    try:
        avisar("codigo", "Preparando a base analítica (metades A e B)")
        base = preparar_base(checagem.con, checagem.arquivos, contexto, entrada.jornada, entrada.periodo_inicio, entrada.periodo_fim)
        consultas = RegistroConsultas(base)
        exe.consultas = consultas
        panorama = consultas.panorama()
        reg.evento("codigo", "panorama", query_id=panorama.query_id, usuarios=base.n_usuarios, eventos=base.n_eventos,
                   dados_hash=base.dados_hash)

        memoria = texto_memoria(decisoes_recentes(contexto.id, ignorar_run=run_id))
        reg.evento("sistema", "memoria_da_squad", decisoes=memoria)
        orq = Orquestrador(cliente, consultas, reg, avisar)
        exe.agente = orq.resultado
        prompt = prompt_rodada_1(
            contexto.texto_para_agente(entrada.jornada), entrada, contexto.jornada(entrada.jornada)["nome"],
            checagem.palpite_status, checagem.lacunas, panorama, descrever_catalogo(base, contexto.eventos(entrada.jornada)),
            memoria=memoria,
        )
        plano = orq.rodada_1(prompt, titulo=f"Dossiê · {contexto.nome} · {run_id}", tags=["dossie-hipoteses", contexto.id, run_id])
        avisar("codigo", f"Executando {min(len(plano.consultas), orq.orcamento)} consultas do plano na metade A")
        resultados = orq.executar_plano(plano)
        saida = orq.rodada_2(resultados)

        avisar("verificador", "Conferindo se todo número tem fonte")
        verificacoes, geral = _verificar(saida, consultas)
        problemas = problemas_para_agente(saida, verificacoes, geral)
        if problemas:
            reg.evento("verificador", "problemas_encontrados", problemas=problemas)
            avisar("verificador", f"{len(problemas)} problema(s): pedindo uma correção ao agente")
            saida = orq.corrigir(problemas)
            verificacoes, geral = _verificar(saida, consultas)
            for v in verificacoes:
                v.refeita = True
        reg.evento("verificador", "verificacao", verificadas=[v.verificada for v in verificacoes],
                   orfaos=[v.orfaos for v in verificacoes], refs_invalidas=[v.refs_invalidas for v in verificacoes],
                   gerais=geral.orfaos)
        exe.verificacao_geral = geral

        avisar("codigo", "Tentando refutar cada candidata na metade B")
        hipoteses = []
        for c, v in zip(saida.candidatas[:MAX_CANDIDATAS], verificacoes):
            rep = refutar(c, consultas)
            hipoteses.append(montar_hipotese(_marcar(c, v), rep, v))
        grupos = ranquear(deduplicar(hipoteses))
        todas = [h for lista in grupos.values() for h in lista]
        reg.evento("codigo", "rotulos", hipoteses=[
            {"id": h.id, "titulo": h.candidata.titulo, "rotulo": h.rotulo.value, "regra": h.regra, "motivo": h.motivo,
             "status": h.status, "query_ids": h.query_ids} for h in todas])

        proximos = []
        if not base.tem_perfil:  # modo comportamental: o "quem" depende de um acesso que a squad ainda não tem
            proximos = [f"Descobrir quem é afetado (faixa etária, segmento, tempo de conta) em “{h.candidata.titulo}”."
                        for h in grupos["principais"] + grupos["outras_evidencias"] if h.rotulo.value == "Evidência"]
            proximos.append(f"Pedir acesso ao perfil ao dono do dado ({contexto.dono_da_fonte('perfil')}), via produto de "
                            "dados governado, com LIA e RIPD antes de qualquer uso.")

        resposta = saida.resposta_ao_palpite
        if resposta and geral.orfaos:
            resposta = marcar_sem_fonte(resposta, geral.orfaos)
        exe.dossie = Dossie(
            meta=MetaExecucao(
                run_id=run_id, parent_run_id=parent_run_id, iniciado_em=agora_iso(), squad=contexto.id,
                contexto_sha=contexto.sha, prompt_versao=PROMPT_VERSAO, prompt_sha=prompt_sha(),
                llm=getattr(cliente, "llm", "roteirizado"), devin_session_id=orq.resultado.session_id,
                devin_url=orq.resultado.url or None, dados_hash=base.dados_hash, semente=SEMENTE if dados_sinteticos else None,
            ),
            entrada=entrada,
            principais=grupos["principais"], outras_evidencias=grupos["outras_evidencias"], research=grupos["research"],
            indicios=grupos["indicios"], descartadas=grupos["descartadas"],
            lacunas=list(dict.fromkeys(checagem.lacunas + list(saida.lacunas))),
            proximos_passos=proximos, memoria=memoria,
            resposta_ao_palpite=resposta,
            avisos=avisos_padrao(consultas, getattr(cliente, "llm", "roteirizado"), dados_sinteticos) + checagem.avisos,
        )
        reg.evento("sistema", "dossie_gerado", principais=[h.id for h in grupos["principais"]])
        avisar("sistema", "Dossiê pronto para a sua revisão")
    except ErroDevin as e:
        exe.erro, exe.erro_tipo = str(e), e.tipo
        reg.evento("sistema", "erro", tipo=e.tipo, detalhe=str(e))
    except ErroProtocolo as e:
        exe.erro, exe.erro_tipo = str(e), "formato"
        reg.evento("sistema", "erro", tipo="formato", detalhe=str(e))
    finally:
        if orq is not None:
            orq.encerrar()
    return exe
