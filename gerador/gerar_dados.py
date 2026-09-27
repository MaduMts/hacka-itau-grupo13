"""Gera os dados sintéticos (tagueamento, FullStory, perfil) com os padrões plantados.

Uso:
    python -m gerador.gerar_dados                     # exemplo "normal" em data/gerados/
    python -m gerador.gerar_dados --variante incompleta

Determinístico: mesma semente e mesmas versões de numpy/pandas geram os mesmos arquivos.
Tudo vetorizado (arrays por sessão), sem laço por evento.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from config import PASTA_DADOS, SEMENTE
from gerador import padroes as P

VERSAO_GERADOR = "1"
EVENTOS = ["card_lock_start", "card_select", "lock_reason_select", "lock_confirm",
           "lock_success", "lock_error", "unlock_start", "unlock_success"]
_EV = {nome: i for i, nome in enumerate(EVENTOS)}
PAGINAS = list(P.MEDIANAS_S)
TIPOS_FS = ["page_view", "click", "dead_click", "rage_click"]
ELEMENTOS = [""] + sorted({e for lista in P.ELEMENTOS.values() for e in lista})
_EL = {nome: i for i, nome in enumerate(ELEMENTOS)}
VARIANTES = ("normal", "incompleta")


@dataclass
class DadosBrutos:
    tagueamento: pd.DataFrame
    fullstory: pd.DataFrame
    perfil: pd.DataFrame


def _escolher(rng: np.random.Generator, opcoes: dict[str, float], n: int) -> np.ndarray:
    probs = np.array(list(opcoes.values()), dtype=float)
    return rng.choice(len(opcoes), size=n, p=probs / probs.sum())


def _lognormal(rng: np.random.Generator, mediana, sigma, n: int) -> np.ndarray:
    return np.exp(np.log(mediana) + sigma * rng.standard_normal(n))


def _inicios(rng: np.random.Generator, n: int) -> np.ndarray:
    """Segundos desde P.INICIO, com mais sessões durante o dia."""
    peso = np.array(P.PESO_HORA, dtype=float)
    dia = rng.integers(0, P.DIAS, n)
    hora = rng.choice(24, size=n, p=peso / peso.sum())
    return (dia * 86400 + hora * 3600 + rng.integers(0, 3600, n)).astype(float)


def gerar(semente: int = SEMENTE, n_usuarios: int = P.N_USUARIOS) -> DadosBrutos:
    rng = np.random.default_rng(semente)
    fim_s = (P.FIM - P.INICIO).total_seconds()

    # --- Usuários (atributos independentes) ---
    n = n_usuarios
    plataformas = list(P.PLATAFORMAS)
    combos = [(p, v) for p in plataformas for v in P.VERSOES[p]]  # (plataforma, versão)
    plat = _escolher(rng, P.PLATAFORMAS, n)
    combo = np.empty(n, dtype=np.int16)
    for ip, nome in enumerate(plataformas):
        m = plat == ip
        deslocamento = sum(len(P.VERSOES[q]) for q in plataformas[:ip])
        combo[m] = _escolher(rng, P.VERSOES[nome], int(m.sum())) + deslocamento
    faixa = _escolher(rng, P.FAIXAS_ETARIAS, n)
    segmento = _escolher(rng, P.SEGMENTOS, n)
    tempo_conta = _escolher(rng, P.TEMPO_DE_CONTA, n)
    ids = [hmac.new(P.SEGREDO_HMAC_EXEMPLO, f"cliente-{i:07d}".encode(), hashlib.sha256).hexdigest()[:16] for i in range(n)]

    c_p1 = combos.index((P.P1["plataforma"], P.P1["versao"]))
    c_beta = combos.index((P.T5["plataforma"], P.T5["versao"]))
    f_60 = list(P.FAIXAS_ETARIAS).index(P.P2["faixa"])

    # --- Sessões de bloqueio: todo usuário tem uma; alguns têm duas ---
    su = np.concatenate([np.arange(n), np.flatnonzero(rng.random(n) < P.P_SEGUNDA_SESSAO)])
    k = len(su)
    t0 = _inicios(rng, k)
    e_p1, e_60, e_beta = combo[su] == c_p1, faixa[su] == f_60, combo[su] == c_beta

    r_sel = rng.random(k) < P.FUNIL_BASE["card_select"]
    r_mot = r_sel & (rng.random(k) < np.where(e_60, P.P2["p_reason"], P.FUNIL_BASE["lock_reason_select"]))
    r_conf = r_mot & (rng.random(k) < np.where(e_p1, P.P1["p_confirm"], P.FUNIL_BASE["lock_confirm"]))
    r_err = r_conf & (rng.random(k) < np.where(e_beta, P.T5["p_erro"], P.P_ERRO_BASE))
    r_suc = r_conf & ~r_err

    ab = P.FATOR_TEMPO_ABANDONO
    d_sel = _lognormal(rng, P.MEDIANAS_S["selecao_cartao"], P.SIGMA_TEMPO, k) * np.where(r_sel, 1, ab)
    d_mot = _lognormal(rng, np.where(e_60, P.P2["mediana_motivo_s"], P.MEDIANAS_S["motivo_bloqueio"]),
                       np.where(e_60, P.P2["sigma_motivo"], P.SIGMA_TEMPO), k) * np.where(r_mot, 1, ab)
    d_conf = _lognormal(rng, np.where(e_p1, P.P1["mediana_confirmacao_s"], P.MEDIANAS_S["confirmacao_bloqueio"]),
                        P.SIGMA_TEMPO, k) * np.where(r_conf, 1, ab)
    d_proc = rng.uniform(*P.PROCESSAMENTO_S, k)
    d_res = _lognormal(rng, P.MEDIANAS_S["resultado_bloqueio"], P.SIGMA_TEMPO, k)
    t1 = t0 + d_sel
    t2 = t1 + d_mot
    t3 = t2 + d_conf
    t4 = t3 + d_proc

    # --- Desbloqueio depois do sucesso (P3: rápido na mesma sessão, igual em todos os grupos) ---
    u = rng.random(k)
    rapido = r_suc & (u < P.P3["p_rapido"])
    tardio = r_suc & (u >= P.P3["p_rapido"]) & (u < P.P3["p_rapido"] + P.P3["p_tardio"])
    v = rng.random(k)
    lim, media = P.P3["limite_min"], P.P3["media_min"]
    t_rap = t4 + 60 * (-media * np.log(1 - v * (1 - np.exp(-lim / media))))  # exponencial truncada
    t_tar = t4 + rng.uniform(P.P3["tardio_min_s"], P.P3["tardio_max_s"], k)
    toque = rng.uniform(*P.DESBLOQUEIO_APOS_TOQUE_S, k)
    suc_unl = rng.random(k) < P.P_SUCESSO_DESBLOQUEIO
    tardio &= (t_tar + toque) <= fim_s
    d_desb = _lognormal(rng, P.MEDIANAS_S["desbloqueio"], P.SIGMA_TEMPO, k)

    # --- Sessões só de desbloqueio (cartão bloqueado antes do período) ---
    uo = np.flatnonzero(rng.random(n) < P.P_SO_DESBLOQUEIO)
    t_uo = _inicios(rng, len(uo))
    toque_uo = rng.uniform(*P.DESBLOQUEIO_APOS_TOQUE_S, len(uo))
    suc_uo = rng.random(len(uo)) < P.P_SUCESSO_DESBLOQUEIO
    d_uo = _lognormal(rng, P.MEDIANAS_S["desbloqueio"], P.SIGMA_TEMPO, len(uo))

    i_tar = np.flatnonzero(tardio)
    s_tar = k + np.arange(len(i_tar))
    s_uo = k + len(i_tar) + np.arange(len(uo))
    sess_usuario = np.concatenate([su, su[i_tar], uo])
    n_sess = len(sess_usuario)
    ids_sessao = [f"{x:016x}" for x in rng.integers(0, 2**62, n_sess, dtype=np.int64)]
    if len(set(ids_sessao)) != n_sess:
        raise RuntimeError("Colisão de session_id; troque a semente.")

    # --- Tagueamento ---
    partes: list[tuple[np.ndarray, np.ndarray, int]] = []

    def evento(sessoes: np.ndarray, t: np.ndarray, nome: str) -> None:
        partes.append((sessoes, t, _EV[nome]))

    todas = np.arange(k)
    evento(todas, t0, "card_lock_start")
    evento(todas[r_sel], t1[r_sel], "card_select")
    evento(todas[r_mot], t2[r_mot], "lock_reason_select")
    evento(todas[r_conf], t3[r_conf], "lock_confirm")
    evento(todas[r_suc], t4[r_suc], "lock_success")
    evento(todas[r_err], t4[r_err], "lock_error")
    ir = todas[rapido]
    evento(ir, t_rap[ir], "unlock_start")
    irs = ir[suc_unl[ir]]
    evento(irs, t_rap[irs] + toque[irs], "unlock_success")
    evento(s_tar, t_tar[i_tar], "unlock_start")
    m = suc_unl[i_tar]
    evento(s_tar[m], t_tar[i_tar][m] + toque[i_tar][m], "unlock_success")
    evento(s_uo, t_uo, "unlock_start")
    evento(s_uo[suc_uo], t_uo[suc_uo] + toque_uo[suc_uo], "unlock_success")

    sess = np.concatenate([p[0] for p in partes])
    ts = np.floor(np.concatenate([p[1] for p in partes])).astype(np.int64)
    ev = np.concatenate([np.full(len(p[0]), p[2], dtype=np.int8) for p in partes])
    ordem = np.lexsort((ev, sess, ts))
    sess, ts, ev = sess[ordem], ts[ordem], ev[ordem]
    tagueamento = _tabela_eventos(sess, ts, sess_usuario, ids, ids_sessao, combos, combo, plat, plataformas)
    tagueamento["event_name"] = pd.Categorical.from_codes(ev, categories=EVENTOS)
    tagueamento = tagueamento[["user_id_hash", "session_id", "timestamp", "event_name", "platform", "app_version"]]

    # --- FullStory: amostra de sessões, uma linha por visita + cliques ---
    amostra = rng.random(n_sess) < P.FULLSTORY_AMOSTRA
    a_lock = amostra[:k]
    visitas = [
        ("selecao_cartao", a_lock, t0, d_sel, r_sel, P.P_DEAD_BASE, P.P_RAGE_BASE, None, None),
        ("motivo_bloqueio", a_lock & r_sel, t1, d_mot, r_mot, P.P_DEAD_BASE,
         np.where(e_60, P.P2["p_rage_motivo"], P.P_RAGE_MOTIVO_BASE), None, (e_60, P.P2["elemento"], P.P2["p_elemento"])),
        ("confirmacao_bloqueio", a_lock & r_mot, t2, d_conf, r_conf,
         np.where(e_p1, P.P1["p_dead_confirmacao"], P.P_DEAD_CONFIRMACAO_BASE), P.P_RAGE_BASE,
         (e_p1, P.P1["elemento"], P.P1["p_elemento"]), None),
        ("resultado_bloqueio", a_lock & r_conf, t4, d_res, np.ones(k, bool), P.P_DEAD_BASE, P.P_RAGE_BASE, None, None),
        ("desbloqueio", a_lock & rapido, t_rap, d_desb, suc_unl, P.P_DEAD_BASE, P.P_RAGE_BASE, None, None),
    ]
    linhas = [_linhas_visita(rng, nome, todas[mask], t[mask], d[mask], av[mask],
                             _sub(pd_, mask), _sub(pr, mask), _sub_esp(ed, mask), _sub_esp(er, mask))
              for nome, mask, t, d, av, pd_, pr, ed, er in visitas]
    linhas.append(_linhas_visita(rng, "desbloqueio", s_tar[amostra[s_tar]], t_tar[i_tar][amostra[s_tar]],
                                 d_desb[i_tar][amostra[s_tar]], suc_unl[i_tar][amostra[s_tar]],
                                 P.P_DEAD_BASE, P.P_RAGE_BASE, None, None))
    linhas.append(_linhas_visita(rng, "desbloqueio", s_uo[amostra[s_uo]], t_uo[amostra[s_uo]],
                                 d_uo[amostra[s_uo]], suc_uo[amostra[s_uo]], P.P_DEAD_BASE, P.P_RAGE_BASE, None, None))
    fs = {chave: np.concatenate([l[chave] for l in linhas]) for chave in linhas[0]}
    ordem = np.lexsort((fs["tipo"], fs["sess"], fs["ts"]))
    fs = {chave: valor[ordem] for chave, valor in fs.items()}
    fullstory = _tabela_eventos(fs["sess"], fs["ts"], sess_usuario, ids, ids_sessao, combos, combo, plat, plataformas)
    fullstory["page"] = pd.Categorical.from_codes(fs["pagina"], categories=PAGINAS)
    fullstory["event_type"] = pd.Categorical.from_codes(fs["tipo"], categories=TIPOS_FS)
    fullstory["element"] = pd.Categorical.from_codes(fs["elemento"], categories=ELEMENTOS)
    fullstory["time_on_page_s"] = np.round(fs["tempo"], 1)
    fullstory = fullstory[["session_id", "user_id_hash", "timestamp", "page", "event_type", "element", "time_on_page_s"]]

    perfil = pd.DataFrame({
        "user_id_hash": ids,
        "faixa_etaria": pd.Categorical.from_codes(faixa, categories=list(P.FAIXAS_ETARIAS)),
        "segmento": pd.Categorical.from_codes(segmento, categories=list(P.SEGMENTOS)),
        "plataforma": pd.Categorical.from_codes(plat, categories=plataformas),
        "tempo_de_conta": pd.Categorical.from_codes(tempo_conta, categories=list(P.TEMPO_DE_CONTA)),
    })
    return DadosBrutos(tagueamento=tagueamento, fullstory=fullstory, perfil=perfil)


def _sub(valor, mask):
    return valor[mask] if isinstance(valor, np.ndarray) else valor


def _sub_esp(especial, mask):
    if especial is None:
        return None
    alvo, elemento, p = especial
    return alvo[mask], elemento, p


def _linhas_visita(rng, pagina, sess, t_entrada, tempo, avancou, p_dead, p_rage, esp_dead, esp_rage) -> dict[str, np.ndarray]:
    """page_view + clique ao avançar + dead/rage click por sorteio, para cada visita à página."""
    n = len(sess)
    ip = PAGINAS.index(pagina)
    elementos = [_EL[e] for e in P.ELEMENTOS[pagina]]

    def sortear_elementos(mask: np.ndarray, especial) -> np.ndarray:
        el = np.array(elementos)[rng.integers(0, len(elementos), n)]
        if especial is not None:
            alvo, elemento, p = especial
            el = np.where(alvo & (rng.random(n) < p), _EL[elemento], el)
        return el[mask]

    m_dead = rng.random(n) < p_dead
    m_rage = rng.random(n) < p_rage
    blocos = [
        (sess, t_entrada, np.full(n, 0), np.full(n, 0), tempo),
        (sess[avancou], (t_entrada + tempo - 0.5)[avancou], np.full(int(avancou.sum()), 1),
         np.full(int(avancou.sum()), _EL[P.CLIQUE_AO_AVANCAR[pagina]]), np.full(int(avancou.sum()), np.nan)),
        (sess[m_dead], (t_entrada + tempo * rng.uniform(0.2, 0.9, n))[m_dead], np.full(int(m_dead.sum()), 2),
         sortear_elementos(m_dead, esp_dead), np.full(int(m_dead.sum()), np.nan)),
        (sess[m_rage], (t_entrada + tempo * rng.uniform(0.2, 0.9, n))[m_rage], np.full(int(m_rage.sum()), 3),
         sortear_elementos(m_rage, esp_rage), np.full(int(m_rage.sum()), np.nan)),
    ]
    return {
        "sess": np.concatenate([b[0] for b in blocos]),
        "ts": np.floor(np.concatenate([b[1] for b in blocos])).astype(np.int64),
        "pagina": np.full(sum(len(b[0]) for b in blocos), ip, dtype=np.int8),
        "tipo": np.concatenate([b[2] for b in blocos]).astype(np.int8),
        "elemento": np.concatenate([b[3] for b in blocos]).astype(np.int16),
        "tempo": np.concatenate([b[4] for b in blocos]).astype(float),
    }


def _tabela_eventos(sess, ts, sess_usuario, ids, ids_sessao, combos, combo, plat, plataformas) -> pd.DataFrame:
    usuario = sess_usuario[sess]
    versoes = sorted({v for _, v in combos})
    versao_do_combo = np.array([versoes.index(v) for _, v in combos])
    return pd.DataFrame({
        "user_id_hash": pd.Categorical.from_codes(usuario, categories=ids),
        "session_id": pd.Categorical.from_codes(sess, categories=ids_sessao),
        "timestamp": np.datetime64(P.INICIO, "s") + ts.astype("timedelta64[s]"),
        "platform": pd.Categorical.from_codes(plat[usuario], categories=plataformas),
        "app_version": pd.Categorical.from_codes(versao_do_combo[combo[usuario]], categories=versoes),
    })


def salvar_csv(dados: DadosBrutos, pasta: Path, arquivos=("tagueamento", "fullstory", "perfil")) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    for nome in arquivos:
        df = getattr(dados, nome)
        con.register("df", df)
        destino = (pasta / f"{nome}.csv").as_posix().replace("'", "''")
        colunas = ", ".join(
            f"CASE WHEN isnan({c}) THEN NULL ELSE {c} END AS {c}" if c == "time_on_page_s" else c for c in df.columns
        )
        con.execute(f"COPY (SELECT {colunas} FROM df) TO '{destino}' (HEADER, DELIMITER ',')")
        con.unregister("df")
    con.close()


def garantir_exemplo(variante: str = "normal", pasta: Path = PASTA_DADOS, semente: int = SEMENTE,
                     n_usuarios: int = P.N_USUARIOS) -> Path:
    """Gera (uma vez) e devolve a pasta com os CSVs do dataset de exemplo da variante."""
    if variante not in VARIANTES:
        raise ValueError(f"Variante '{variante}' desconhecida. Válidas: {', '.join(VARIANTES)}")
    sufixo = "" if n_usuarios == P.N_USUARIOS else f"_{n_usuarios}"
    marca = {"versao_gerador": VERSAO_GERADOR, "semente": semente, "n_usuarios": n_usuarios}

    def pronta(destino: Path) -> bool:
        arq = destino / "_gerado.json"
        return arq.exists() and json.loads(arq.read_text(encoding="utf-8")) == {**marca, "variante": destino.name}

    normal = pasta / f"exemplo_normal{sufixo}"
    if not pronta(normal):
        salvar_csv(gerar(semente, n_usuarios), normal)
        (normal / "_gerado.json").write_text(json.dumps({**marca, "variante": normal.name}), encoding="utf-8")
    if variante == "normal":
        return normal

    destino = pasta / f"exemplo_{variante}{sufixo}"
    if not pronta(destino):
        if destino.exists():
            shutil.rmtree(destino)
        destino.mkdir(parents=True)
        if variante == "incompleta":  # sem perfil.csv
            for nome in ("tagueamento", "fullstory"):
                shutil.copyfile(normal / f"{nome}.csv", destino / f"{nome}.csv")
        (destino / "_gerado.json").write_text(json.dumps({**marca, "variante": destino.name}), encoding="utf-8")
    return destino


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gera o dataset sintético de exemplo.")
    parser.add_argument("--variante", default="normal", choices=VARIANTES)
    parser.add_argument("--n", type=int, default=P.N_USUARIOS, help="número de usuários")
    args = parser.parse_args()
    pasta = garantir_exemplo(args.variante, n_usuarios=args.n)
    for arq in sorted(pasta.glob("*.csv")):
        print(f"{arq}  ({arq.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
