"""Contexto da empresa e da squad: o agente "já nasce" dentro da squad.

A PM não redigita contexto. Ele vem de arquivos versionados (`contexto/empresa.toml`
e `contexto/squads/<id>.toml`) e é montado de forma determinística em texto para o
agente: mesmos arquivos → mesmo texto → mesmo hash no registro.
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from config import PASTA_CONTEXTO, SQUAD_PADRAO

# Ordem fixa das políticas no texto (as chaves *_id vão só para o registro).
_POLITICAS_NO_TEXTO = (
    "somente_agregados", "supressao", "dimensoes_por_tabela", "pseudonimizacao", "correlacao",
    "nps", "recortes_idade", "uso_proibido", "perfil_opcional",
)


@dataclass(frozen=True)
class ContextoSquad:
    empresa: dict[str, Any]
    squad: dict[str, Any]

    @property
    def id(self) -> str:
        return self.squad["id"]

    @property
    def nome(self) -> str:
        return self.squad["nome"]

    @property
    def sha(self) -> str:
        canonico = json.dumps(
            {"empresa": self.empresa, "squad": self.squad}, sort_keys=True, ensure_ascii=False, default=str
        )
        return hashlib.sha256(canonico.encode("utf-8")).hexdigest()[:16]

    def jornadas(self) -> list[dict[str, Any]]:
        return list(self.squad.get("jornadas", []))

    def jornada(self, jornada_id: str) -> dict[str, Any]:
        for j in self.jornadas():
            if j["id"] == jornada_id:
                return j
        validas = ", ".join(j["id"] for j in self.jornadas())
        raise KeyError(f"Jornada '{jornada_id}' não existe na squad {self.id}. Válidas: {validas}")

    def eventos(self, jornada_id: str) -> list[str]:
        j = self.jornada(jornada_id)
        eventos = list(j["eventos_funil"])
        if j.get("evento_erro"):
            eventos.append(j["evento_erro"])
        return eventos + list(j.get("eventos_extras", []))

    def paginas(self, jornada_id: str) -> list[str]:
        return list(self.jornada(jornada_id)["paginas"])

    def pares_reversiveis(self, jornada_id: str) -> list[tuple[str, str]]:
        return [(a, b) for a, b in self.jornada(jornada_id).get("pares_reversiveis", [])]

    def sinonimos(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.squad.get("sinonimos", {}).items()}

    def politica(self, chave: str) -> Any:
        return self.empresa.get("politicas_dados", {}).get(chave)

    def dono_da_fonte(self, fonte: str) -> str:
        return self.empresa.get("fontes", {}).get(fonte, {}).get("dono", "não informado")

    def texto_para_agente(self, jornada_id: str) -> str:
        """Contexto que acompanha toda sessão do agente (sem dado de cliente nem segredo)."""
        e, s, j = self.empresa, self.squad, self.jornada(jornada_id)
        pol = e.get("politicas_dados", {})
        linhas = [f"## Empresa: {e['nome']} (contexto versão {e['versao']})", "", "Políticas de dados que você deve seguir:"]
        linhas += [f"- {pol[k]}" for k in _POLITICAS_NO_TEXTO if k in pol]
        linhas += ["", "Glossário:"]
        linhas += [f"- {termo}: {definicao}" for termo, definicao in e.get("glossario", {}).items()]
        linhas += ["", "Fontes de dados:"]
        linhas += [f"- {nome} (dono: {f['dono']}): {f['descricao']}" for nome, f in e.get("fontes", {}).items()]
        linhas += ["", "Segmentos (nomes fictícios):"]
        linhas += [f"- {nome}: {desc}" for nome, desc in e.get("segmentos", {}).items()]

        linhas += ["", f"## Squad: {s['nome']} ({s['produto']}, contexto versão {s['versao']})", f"Missão: {s['missao']}", "Metas:"]
        linhas += [f"- {m['descricao']}: {m['indicador']}; alvo {m['alvo']}" for m in s.get("metas", [])]

        linhas += ["", f"## Jornada em análise: {j['nome']} (id {j['id']})"]
        linhas.append("Funil, em ordem: " + " → ".join(j["eventos_funil"]))
        if j.get("evento_erro"):
            linhas.append(f"Evento de erro: {j['evento_erro']}")
        if j.get("eventos_extras"):
            linhas.append("Outros eventos: " + ", ".join(j["eventos_extras"]))
        linhas += ["", "Dicionário de eventos:"]
        dicionario = s.get("dicionario_eventos", {})
        for ev in self.eventos(jornada_id):
            info = dicionario.get(ev, {})
            linhas.append(f"- {ev} (página {info.get('pagina', '?')}): {info.get('descricao', 'sem descrição')}")
        linhas += ["", "Páginas e elementos:"]
        paginas = s.get("paginas", {})
        for pg in j["paginas"]:
            info = paginas.get(pg, {})
            elementos = ", ".join(info.get("elementos", []))
            linhas.append(f"- {pg}: {info.get('descricao', '')}. Elementos: {elementos}")
        if j.get("pares_reversiveis"):
            pares = "; ".join(f"{a} → {b}" for a, b in j["pares_reversiveis"])
            linhas += ["", f"Pares reversíveis (ação que pode ser desfeita): {pares}"]
        if s.get("versoes_app"):
            linhas += ["", "Linha do tempo de versões do app:"]
            for v in s["versoes_app"]:
                lanc = v["lancamento"]
                lanc_txt = lanc.strftime("%d/%m/%Y") if isinstance(lanc, date) else str(lanc)
                linhas.append(f"- {v['plataforma']} {v['versao']} ({lanc_txt}): {v['nota']}")
        return "\n".join(linhas)


def _ler_toml(caminho: Path) -> dict[str, Any]:
    with caminho.open("rb") as f:
        return tomllib.load(f)


def listar_squads(pasta: Path = PASTA_CONTEXTO) -> list[str]:
    return sorted(p.stem for p in (pasta / "squads").glob("*.toml"))


def carregar_contexto(squad_id: str = SQUAD_PADRAO, pasta: Path = PASTA_CONTEXTO) -> ContextoSquad:
    caminho = pasta / "squads" / f"{squad_id}.toml"
    if not caminho.exists():
        raise KeyError(f"Squad '{squad_id}' não encontrada. Disponíveis: {', '.join(listar_squads(pasta))}")
    return ContextoSquad(empresa=_ler_toml(pasta / "empresa.toml"), squad=_ler_toml(caminho))
