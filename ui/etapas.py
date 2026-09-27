"""Telas do app (página única): entrada → checagem → execução → resultado (revisão, dossiê, registro)."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from agente.cliente_devin import ClienteDevin, ClienteRoteirizado, ErroDevin
from agente.roteiros import roteiro_gabarito
from config import PASTA_DADOS, SQUAD_PADRAO, chave_devin
from contexto.carregar import ContextoSquad, carregar_contexto, listar_squads
from contratos import DecisaoPM, EntradaPM, Hipotese
from dossie import ACAO, CAMPOS_TEXTO, decisao_de, gerar_markdown, gerar_pedido_research, resumo_verificacao, textos
from entrada.checagem import checar_entrada
from gerador.gerar_dados import garantir_exemplo
from motor.carga import localizar_arquivos
from pipeline import Execucao, rodar_execucao
from registro import agora_iso
from ui.componentes import dialogo_consulta, metricas_hipotese, selo
from verificacao.numeros import verificar_candidata

CENARIOS = {"normal": "Normal (todos os arquivos)", "incompleta": "Incompleta (sem perfil.csv)"}
MODOS = {"roteirizado": "Sem rede (agente roteirizado)", "ao_vivo": "Ao vivo (Devin)"}
ERROS = {
    "autenticacao": "A chave do Devin foi recusada. Confira a DEVIN_API_KEY no .env ou nos Secrets.",
    "cota": "A cota do Devin desta chave acabou. Troque a chave (outra licença) ou use o modo sem rede.",
    "limite": "O Devin limitou as requisições. Aguarde um pouco ou use o modo sem rede.",
    "timeout": "O agente demorou mais que o limite desta rodada.",
    "rede": "Falha de rede falando com o Devin.",
    "sessao_encerrada": "A sessão do agente foi encerrada antes de terminar.",
    "formato": "O agente não publicou a resposta no formato combinado.",
    "config": "Falta configurar a chave do Devin.",
}


# --- Estado e recursos ---


def _estado() -> None:
    padrao = {"etapa": "entrada", "entrada": None, "arquivos": None, "checagem": None, "execucao": None,
              "modo": "roteirizado", "fonte": "exemplo:normal", "parent_run_id": None, "executar": False,
              "erro_execucao": None}
    for chave, valor in padrao.items():
        st.session_state.setdefault(chave, valor)


@st.cache_resource(show_spinner="Gerando os dados sintéticos de exemplo (uma vez só)…")
def _pasta_exemplo(variante: str) -> Path:
    return garantir_exemplo(variante)


def _contexto() -> ContextoSquad:
    squad = st.query_params.get("squad", SQUAD_PADRAO)
    return carregar_contexto(squad if squad in listar_squads() else SQUAD_PADRAO)


def _ir(etapa: str) -> None:
    st.session_state.etapa = etapa
    st.rerun()


def _salvar_uploads(enviados: dict) -> dict[str, Path]:
    h = hashlib.sha256()
    for nome in sorted(enviados):
        h.update(nome.encode())
        h.update(enviados[nome].getvalue())
    pasta = PASTA_DADOS / "uploads" / h.hexdigest()[:12]
    pasta.mkdir(parents=True, exist_ok=True)
    for nome, arquivo in enviados.items():
        (pasta / f"{nome}.csv").write_bytes(arquivo.getvalue())
    return localizar_arquivos(pasta)


def _cliente(modo: str):
    return ClienteDevin(chave_devin()) if modo == "ao_vivo" else ClienteRoteirizado(roteiro_gabarito)


# --- Cabeçalho e barra lateral ---


def cabecalho(ctx: ContextoSquad) -> None:
    st.markdown(":material/science: **Protótipo de hackathon** · dados fictícios · envio à Research simulado")
    st.title("Dossiê de Hipóteses")
    st.caption(f"{ctx.nome} · {ctx.squad['produto']} · o agente já trabalha com o contexto desta squad")
    with st.sidebar:
        st.subheader("Agente Dossiê de Hipóteses")
        st.caption("Transforma dados de tagueamento e FullStory em hipóteses priorizadas, com a fonte de cada número.")
        st.markdown(f"**Squad:** {ctx.nome}  \n**Contexto:** versão {ctx.squad['versao']} · `{ctx.sha}`")
        exe: Execucao | None = st.session_state.execucao
        if exe is not None:
            st.markdown(f"**Execução:** `{exe.run_id}`")
            if exe.agente and exe.agente.url:
                st.markdown(f"[Ver a sessão no Devin]({exe.agente.url})")
        if st.session_state.etapa != "entrada" and st.button("Nova análise", icon=":material/restart_alt:"):
            for chave in ("entrada", "arquivos", "checagem", "execucao", "parent_run_id", "erro_execucao"):
                st.session_state[chave] = None
            st.session_state.executar = False
            _ir("entrada")


# --- Tela 1: entrada ---


def tela_entrada(ctx: ContextoSquad) -> None:
    with st.expander("O que o agente já sabe sobre a squad (carregado automaticamente)", icon=":material/menu_book:"):
        jornadas = ctx.jornadas()
        st.caption("A PM não precisa digitar contexto: ele vem da configuração da squad e da empresa.")
        st.code(ctx.texto_para_agente(jornadas[0]["id"]), language=None)

    anterior: EntradaPM | None = st.session_state.entrada
    with st.form("form_entrada"):
        c1, c2 = st.columns([2, 1])
        jornadas = {j["id"]: j["nome"] for j in ctx.jornadas()}
        jornada = c1.selectbox("Jornada a investigar", list(jornadas), format_func=jornadas.get)
        pm = c2.text_input("Seu nome (vai para o registro)", value=anterior.pm if anterior else "PM da Squad Cartões")
        c3, c4 = st.columns([1, 2])
        periodo = c3.date_input("Período", value=(anterior.periodo_inicio, anterior.periodo_fim) if anterior
                                else (date(2026, 8, 1), date(2026, 8, 30)), format="DD/MM/YYYY")
        palpite = c4.text_input("Seu palpite (opcional)", value=(anterior.palpite or "") if anterior else "",
                                placeholder="ex.: acho que idosos travam na confirmação")
        fonte = st.radio("Dados", ["exemplo", "upload"], horizontal=True,
                         format_func={"exemplo": "Dados de exemplo (sintéticos)", "upload": "Enviar CSVs"}.get)
        cenario = st.selectbox("Cenário de exemplo", list(CENARIOS), format_func=CENARIOS.get,
                               help="Só vale para os dados de exemplo.")
        enviados = {}
        with st.expander("Enviar CSVs (tagueamento obrigatório; os demais opcionais)"):
            for nome in ("tagueamento", "fullstory", "perfil", "nps"):
                enviados[nome] = st.file_uploader(f"{nome}.csv", type="csv", key=f"up_{nome}")
        modos = list(MODOS) if chave_devin() else ["roteirizado"]
        modo = st.radio("Agente", modos, format_func=MODOS.get, horizontal=True,
                        index=modos.index(st.session_state.modo) if st.session_state.modo in modos else 0,
                        help=None if chave_devin() else "Sem DEVIN_API_KEY configurada: só o modo sem rede está disponível.")
        enviar = st.form_submit_button("Checar entrada", type="primary", icon=":material/fact_check:")

    if not enviar:
        return
    if not isinstance(periodo, tuple) or len(periodo) != 2:
        st.error("Escolha a data inicial e a final do período.")
        return
    if fonte == "exemplo":
        arquivos = localizar_arquivos(_pasta_exemplo(cenario))
        rotulo_fonte = f"exemplo:{cenario}"
    else:
        presentes = {nome: arq for nome, arq in enviados.items() if arq is not None}
        if not presentes:
            st.error("Envie pelo menos o tagueamento.csv, ou use os dados de exemplo.")
            return
        arquivos, rotulo_fonte = _salvar_uploads(presentes), "upload"
    entrada = EntradaPM(squad=ctx.id, jornada=jornada, periodo_inicio=periodo[0], periodo_fim=periodo[1],
                        palpite=palpite.strip() or None, pm=pm.strip() or "PM", fonte_dados=rotulo_fonte)
    with st.spinner("Checando os arquivos…"):
        st.session_state.checagem = checar_entrada(arquivos, ctx, jornada, periodo[0], periodo[1], entrada.palpite)
    st.session_state.update(entrada=entrada, arquivos=arquivos, modo=modo, fonte=rotulo_fonte)
    _ir("checagem")


# --- Tela 2: checagem ---


def tela_checagem(ctx: ContextoSquad) -> None:
    chk = st.session_state.checagem
    st.subheader("Checagem da entrada")
    for texto in chk.ok:
        st.success(texto, icon=":material/check_circle:")
    for texto in chk.bloqueios:
        st.error(texto, icon=":material/block:")
    for texto in chk.avisos:
        st.warning(texto, icon=":material/warning:")
    for texto in chk.lacunas:
        st.info(f"Lacuna declarada: {texto}", icon=":material/help_outline:")
    if chk.sugestoes_palpite:
        st.markdown("**Palpites mais úteis** (citam etapa, grupo ou métrica):\n" + "\n".join(f"- {s}" for s in chk.sugestoes_palpite))

    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Voltar", icon=":material/arrow_back:"):
        _ir("entrada")
    if c2.button("Gerar dossiê", type="primary", disabled=not chk.pode_seguir, icon=":material/play_arrow:"):
        st.session_state.executar = True
        _ir("execucao")
    if not chk.pode_seguir:
        st.caption("Corrija os bloqueios acima para seguir.")


# --- Tela 3: execução ---


def tela_execucao(ctx: ContextoSquad) -> None:
    """Roda o pipeline uma vez por pedido (flag `executar`); reruns só mostram o resultado ou o erro."""
    entrada: EntradaPM = st.session_state.entrada
    st.subheader("Gerando o dossiê")
    st.caption(f"Agente: {MODOS[st.session_state.modo]} · o código executa todas as consultas; o agente só vê agregados.")
    if st.session_state.executar:
        st.session_state.update(executar=False, erro_execucao=None)
        exe = None
        inicio = time.monotonic()
        with st.status("Trabalhando…", expanded=True) as status:
            linha_agente = st.empty()

            def ao_progresso(etapa: str, mensagem: str) -> None:
                if etapa == "agente":
                    linha_agente.markdown(f":material/smart_toy: {mensagem}")
                elif etapa == "sessao":
                    st.markdown(f":material/open_in_new: [Sessão do agente no Devin]({mensagem})")
                else:
                    icone = {"consulta": ":material/query_stats:", "verificador": ":material/rule:"}.get(etapa, ":material/check:")
                    st.markdown(f"{icone} {mensagem}")

            try:
                exe = rodar_execucao(entrada, st.session_state.checagem, ctx, _cliente(st.session_state.modo), ao_progresso,
                                     parent_run_id=st.session_state.parent_run_id,
                                     dados_sinteticos=st.session_state.fonte.startswith("exemplo"))
            except ErroDevin as e:  # erro ao montar o cliente (ex.: sem chave)
                st.session_state.erro_execucao = ERROS.get(e.tipo, str(e))
            except Exception as e:  # noqa: BLE001 — mostrar qualquer falha inesperada sem derrubar o app
                st.session_state.erro_execucao = f"Erro inesperado ao gerar o dossiê: {type(e).__name__}: {e}"
                st.exception(e)
            if exe is not None and exe.erro:
                st.session_state.erro_execucao = f"{ERROS.get(exe.erro_tipo or '', '')} Detalhe: {exe.erro}".strip()
            if st.session_state.erro_execucao:
                status.update(label="Não foi possível gerar o dossiê", state="error")
            else:
                status.update(label=f"Dossiê pronto em {time.monotonic() - inicio:.0f} s", state="complete", expanded=False)
        if exe is not None:
            st.session_state.execucao = exe
        if not st.session_state.erro_execucao:
            _ir("resultado")

    if st.session_state.erro_execucao:
        st.error(st.session_state.erro_execucao)
        c1, c2, c3, _ = st.columns([1, 1.3, 1.2, 2])
        if c1.button("Tentar de novo", icon=":material/refresh:"):
            st.session_state.executar = True
            st.rerun()
        if st.session_state.modo == "ao_vivo" and c2.button("Usar modo sem rede", icon=":material/wifi_off:"):
            st.session_state.update(modo="roteirizado", executar=True)
            st.rerun()
        if c3.button("Voltar à entrada", icon=":material/arrow_back:"):
            _ir("entrada")


# --- Tela 4: resultado ---


def _registrar_decisao(exe: Execucao, h: Hipotese, acao: str, comentario: str, editados: dict[str, str]) -> DecisaoPM:
    verificacao = None
    if acao == "editar" and editados:
        verificacao = verificar_candidata(h.candidata.model_copy(update=editados), exe.consultas)
    decisao = DecisaoPM(hipotese_id=h.id, acao=acao, pm=exe.entrada.pm, comentario=comentario,  # type: ignore[arg-type]
                        textos_editados=editados, ts=agora_iso(), verificacao_edicao=verificacao)
    exe.dossie.decisoes.append(decisao)
    exe.registro.evento(
        "pm", "decisao", hipotese_id=h.id, titulo=h.candidata.titulo, rotulo=h.rotulo.value, acao=acao, comentario=comentario,
        textos_originais={k: getattr(h.candidata, k) for k in editados}, textos_editados=editados,
        numeros_sem_fonte_na_edicao=verificacao.orfaos if verificacao else [],
    )
    return decisao


def cartao(exe: Execucao, h: Hipotese) -> None:
    decisao = decisao_de(exe.dossie, h.id)
    t = textos(h, decisao)
    with st.container(border=True):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"#### {h.id} · {t['titulo']}")
        with c2:
            selo(h.rotulo)
        st.write(t["enunciado"])
        metricas_hipotese(h)
        st.caption(f"**Por que este rótulo:** {h.motivo} (regra {h.regra})")

        fontes = [h.query_ids[k] for k in ("A", "B", "T") if k in h.query_ids]
        fontes += [q for q in h.candidata.query_ids_citados if q not in fontes]
        colunas = st.columns(max(len(fontes), 1) + 3)
        colunas[0].markdown("**Fontes:**")
        for coluna, qid in zip(colunas[1:], fontes):
            if coluna.button(qid, key=f"fonte-{h.id}-{qid}", icon=":material/database:"):
                dialogo_consulta(exe.consultas.obter(qid))

        d1, d2 = st.columns(2)
        d1.markdown(f"**O que o dado responde**  \n{t['o_que_o_dado_responde']}")
        d2.markdown(f"**O que só a Research responde**  \n{t['o_que_so_a_research_responde']}  \n"
                    f"_Pergunta sugerida:_ {t['pergunta_para_research']}")
        v = h.verificacao
        if v.verificada:
            st.caption(f":material/check_circle: Verificação: {resumo_verificacao(v)}.")
        else:
            st.warning("Não verificada: " + "; ".join(v.orfaos + v.refs_invalidas), icon=":material/warning:")
        if h.evidencias_complementares:
            st.caption("Evidências complementares: " + "; ".join(h.evidencias_complementares))

        if decisao:
            st.info(f"Decisão registrada: **{ACAO[decisao.acao]}** por {decisao.pm} em {decisao.ts}"
                    + (f" — {decisao.comentario}" if decisao.comentario else ""), icon=":material/how_to_reg:")
            if decisao.verificacao_edicao and decisao.verificacao_edicao.orfaos:
                st.warning("A edição trouxe números sem fonte: " + "; ".join(decisao.verificacao_edicao.orfaos)
                           + ". A decisão é sua; isso fica no registro.", icon=":material/warning:")
        with st.expander("Registrar sua decisão" if not decisao else "Mudar a decisão"):
            acao = st.segmented_control("Decisão", list(ACAO), format_func=ACAO.get, key=f"acao-{h.id}")
            editados: dict[str, str] = {}
            if acao == "editar":
                rotulos = {"titulo": "Título", "enunciado": "Enunciado", "o_que_o_dado_responde": "O que o dado responde",
                           "o_que_so_a_research_responde": "O que só a Research responde",
                           "pergunta_para_research": "Pergunta para a Research"}
                for campo in CAMPOS_TEXTO:
                    novo = st.text_area(rotulos[campo], value=t[campo], key=f"ed-{h.id}-{campo}")
                    if novo != getattr(h.candidata, campo):
                        editados[campo] = novo
            comentario = st.text_input("Comentário (opcional)" if acao != "mais_analise" else "O que você quer que o agente investigue?",
                                       key=f"com-{h.id}")
            if st.button("Registrar", key=f"reg-{h.id}", disabled=acao is None, type="primary"):
                _registrar_decisao(exe, h, acao, comentario.strip(), editados)
                if acao == "mais_analise":
                    _pedir_mais_analise(exe, comentario.strip())
                st.rerun()


def _pedir_mais_analise(exe: Execucao, pedido: str) -> None:
    """Nova execução ligada à atual, com o pedido da PM como palpite."""
    ctx = _contexto()
    entrada = exe.entrada.model_copy(update={"palpite": pedido or exe.entrada.palpite})
    st.session_state.update(entrada=entrada, parent_run_id=exe.run_id)
    st.session_state.checagem = checar_entrada(st.session_state.arquivos, ctx, entrada.jornada,
                                               entrada.periodo_inicio, entrada.periodo_fim, entrada.palpite)
    st.session_state.etapa = "checagem"


def aba_revisao(exe: Execucao) -> None:
    d = exe.dossie
    with st.container(border=True):
        st.markdown("#### Seu palpite")
        st.write(f"“{d.entrada.palpite}”" if d.entrada.palpite else "Sem palpite: exploração aberta.")
        st.markdown(d.resposta_ao_palpite or "_O agente não avaliou o palpite._")
    st.markdown("### As 2 hipóteses mais fortes")
    if not d.principais:
        st.info("Nenhuma hipótese sustentada pelos dados nesta execução. Veja as lacunas e os outros achados.")
    for h in d.principais:
        cartao(exe, h)
    for titulo, lista in (("Hipóteses para a Research", d.research), ("Outras evidências", d.outras_evidencias)):
        if lista:
            st.markdown(f"### {titulo}")
            for h in lista:
                cartao(exe, h)
    for titulo, lista, aberto in (("Indícios (não viram evidência)", d.indicios, False),
                                  ("Descartadas pelo código e por quê", d.descartadas, True)):
        if not lista:
            continue
        with st.expander(f"{titulo} ({len(lista)})", expanded=aberto):
            for h in lista:
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"**{h.id} · {h.candidata.titulo}**  \n{h.motivo}")
                with c2:
                    selo(h.rotulo)
    if d.lacunas:
        st.markdown("### Lacunas")
        for lacuna in d.lacunas:
            st.info(lacuna, icon=":material/help_outline:")
    with st.expander("Avisos"):
        for aviso in d.avisos:
            st.caption(f"• {aviso}")


def _apendice(exe: Execucao) -> dict[str, str]:
    usados = ["Q00-A"] + [q for h in _todas(exe) for q in h.query_ids.values()]
    return {q: exe.consultas.obter(q).texto_llm for q in dict.fromkeys(usados) if exe.consultas.obter(q)}


def _todas(exe: Execucao) -> list[Hipotese]:
    d = exe.dossie
    return d.principais + d.outras_evidencias + d.research + d.indicios + d.descartadas


def aba_dossie(exe: Execucao, ctx: ContextoSquad) -> None:
    nome_jornada = ctx.jornada(exe.entrada.jornada)["nome"]
    md = gerar_markdown(exe.dossie, ctx.nome, nome_jornada, _apendice(exe))
    st.download_button("Baixar dossiê (.md)", md, file_name=f"dossie_{exe.run_id}.md", mime="text/markdown",
                       icon=":material/download:",
                       on_click=lambda: exe.registro.evento("pm", "dossie_baixado", formato="md"))
    st.markdown(md)


def aba_research(exe: Execucao, ctx: ContextoSquad) -> None:
    nome_jornada = ctx.jornada(exe.entrada.jornada)["nome"]
    pedido = gerar_pedido_research(exe.dossie, ctx.empresa["research"]["modelo_pedido"], ctx.nome, nome_jornada)
    st.caption("Simulado: o texto é gerado para a Research, mas nada é enviado. Só entram hipóteses que você aceitou.")
    if not pedido:
        st.info("Aceite ao menos uma hipótese na aba Revisão para gerar o pedido.")
        return
    st.code(pedido, language=None)
    enviados = [e for e in exe.registro.ler() if e["evento"] == "pedido_research_simulado"]
    if st.button("Encaminhar à Research (simulado)", type="primary", icon=":material/send:"):
        exe.registro.evento("pm", "pedido_research_simulado", pedido=pedido)
        st.rerun()
    if enviados:
        st.success(f"Pedido registrado como encaminhado em {enviados[-1]['ts']} (simulação: nada saiu do protótipo).")


def aba_registro(exe: Execucao) -> None:
    eventos = exe.registro.ler()
    st.caption("Registro append-only desta execução: quem fez o quê e quando, com versões de contexto e prompt. "
               "Não há como apagar pela tela.")
    tabela = pd.DataFrame([{"quando": e["ts"], "quem": e["ator"], "evento": e["evento"],
                            "detalhes": json.dumps(e["dados"], ensure_ascii=False)[:300]} for e in eventos])
    st.dataframe(tabela, hide_index=True)
    st.download_button("Baixar registro (.jsonl)", exe.registro.como_jsonl(), file_name=f"registro_{exe.run_id}.jsonl",
                       mime="application/jsonl", icon=":material/download:")


def aba_enviado(exe: Execucao) -> None:
    st.caption("Texto exato enviado ao agente: contexto da squad, panorama e tabelas agregadas. Nenhuma linha individual, "
               "nenhum identificador de cliente.")
    for texto in exe.agente.textos_enviados if exe.agente else []:
        with st.expander(f"Rodada {texto['rodada']} · {'prompt inicial' if texto['tipo'] == 'prompt' else 'mensagem'}"):
            st.code(texto["texto"], language=None)


def aba_consultas(exe: Execucao) -> None:
    for qid, res in exe.consultas.resultados.items():
        if st.button(f"{qid} · {res.ferramenta} · metade {res.metade}", key=f"consulta-{qid}", icon=":material/database:"):
            dialogo_consulta(res)


def tela_resultado(ctx: ContextoSquad) -> None:
    exe: Execucao = st.session_state.execucao
    abas = st.tabs(["Revisão", "Dossiê", "Pedido à Research", "Registro", "O que foi enviado ao agente", "Consultas"])
    with abas[0]:
        aba_revisao(exe)
    with abas[1]:
        aba_dossie(exe, ctx)
    with abas[2]:
        aba_research(exe, ctx)
    with abas[3]:
        aba_registro(exe)
    with abas[4]:
        aba_enviado(exe)
    with abas[5]:
        aba_consultas(exe)


def principal() -> None:
    _estado()
    ctx = _contexto()
    cabecalho(ctx)
    etapa = st.session_state.etapa
    if etapa == "entrada" or st.session_state.entrada is None:
        tela_entrada(ctx)
    elif etapa == "checagem":
        tela_checagem(ctx)
    elif etapa == "execucao":
        tela_execucao(ctx)
    else:
        tela_resultado(ctx)
