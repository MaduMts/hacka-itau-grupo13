"""Verificador: todo número escrito pelo agente precisa ter fonte numa consulta citada.

- Só valem as consultas citadas na candidata (evidência principal, `query_ids_citados` e
  qualquer `Qnn-X` no texto). Número que só existe numa consulta não citada é órfão.
- Antes de extrair, mascara o que não é resultado: query_ids, versões, datas, rótulos de grupo
  (`60+`, `18-24`, `android 8.4.0`), IC95, janelas usadas ("10 min"), amostra ("10%", "×10").
- O casamento respeita a precisão escrita, mais meia casa da precisão exibida na tabela
  (arredondamento duplo): "12%" casa com 12,34%, "13%" não. "Cerca de" aceita ±5%.
- Também confere as referências (consulta, grupo, métrica) e a causalidade no enunciado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from contratos import Candidata, ResultadoConsulta, SaidaDevin, Verificacao
from motor.registro_consultas import RegistroConsultas

NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[,.]\d+)?"
CAUSAL = re.compile(
    r"\b(porque|pois|devido|por causa|causad[oa]s?|leva(?:m)? a|faz(?:em)? com que|em raz[aã]o de|gra[cç]as a|explica(?:m)?)\b",
    re.IGNORECASE,
)
_APROX = re.compile(r"(cerca de|aproximadamente|aprox\.?|~|≈|quase|mais de|menos de|por volta de|em torno de)\s*$", re.IGNORECASE)
_QID = re.compile(r"\bQ\d{2}-[ABT]\b")
_ISENTOS_FIXOS = [
    r"\bQ\d{2}-[ABT]\b",
    r"\b\d+\.\d+\.\d+(?:-[A-Za-z0-9]+)?\b",
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\bIC\s?9[59]\s?%?",
    r"\b9[59]\s?% de confiança",
    r"amostra de \d+\s?%",
    r"\d+\s?% das sessões",
    r"[×x]\s?10\b",
    r"(?:menos de|mínimo de|pelo menos|n\s*[<>≥≤=]+)\s*\d+",
    r"lift\s*[≥>]=?\s*1[,.]5",
]
_TOKEN = re.compile(
    r"(?<![\w.,])(?:"
    r"(?P<cada>(?P<cada_n>\d+)\s+(?:em|de)\s+cada\s+(?P<cada_m>\d+))"
    rf"|(?P<pp>(?P<pp_n>{NUM})\s?(?:p\.?\s?p\.?|pontos? percentua(?:l|is)))"
    rf"|(?P<pct>(?P<pct_n>{NUM})\s?(?:%|por cento))"
    rf"|(?P<mult>(?P<mult_n>{NUM})\s?(?:x|×|vezes)(?![A-Za-zÀ-ú]))"
    rf"|(?P<mil>(?P<mil_n>{NUM})\s?(?P<mil_u>mil|milh[ãa]o|milh[õo]es)\b)"
    rf"|(?P<tempo>(?P<tempo_n>{NUM})\s?(?P<tempo_u>segundos?|seg|s|minutos?|min)\b)"
    rf"|(?P<livre>{NUM})"
    r")",
    re.IGNORECASE,
)
_CAMPOS = ("titulo", "enunciado", "o_que_o_dado_responde", "o_que_so_a_research_responde", "pergunta_para_research")


@dataclass
class Numero:
    texto: str
    valor: float
    tipo: str  # pct | pp | mult | contagem | tempo | livre
    tolerancia: float
    aproximado: bool


def _ler(num: str) -> tuple[float, int]:
    """Valor e casas decimais escritas. Ponto seguido de 3 dígitos é milhar; vírgula é decimal."""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", num):
        inteiro, _, dec = num.partition(",")
        return float(inteiro.replace(".", "") + (f".{dec}" if dec else "")), len(dec)
    if "," in num:
        inteiro, dec = num.split(",", 1)
        return float(f"{inteiro}.{dec}"), len(dec)
    if "." in num:
        inteiro, dec = num.split(".", 1)
        return (float(inteiro + dec), 0) if len(dec) == 3 else (float(num), len(dec))
    return float(num), 0


def mascarar(texto: str, rotulos: list[str]) -> str:
    """Troca por espaços (mesmo tamanho) tudo que não é número de resultado."""
    def apagar(m: re.Match) -> str:
        return " " * len(m.group(0))

    for rotulo in sorted(set(rotulos), key=len, reverse=True):
        if any(ch.isdigit() for ch in rotulo):
            texto = re.sub(re.escape(rotulo), apagar, texto, flags=re.IGNORECASE)
    for padrao in _ISENTOS_FIXOS:
        texto = re.sub(padrao, apagar, texto, flags=re.IGNORECASE)
    return texto


def extrair_numeros(texto: str, rotulos: list[str]) -> list[Numero]:
    limpo = mascarar(texto, rotulos)
    numeros = []
    for m in _TOKEN.finditer(limpo):
        aproximado = bool(_APROX.search(limpo[max(0, m.start() - 25):m.start()]))
        bruto = texto[m.start():m.end()]
        if m.group("cada"):
            n, total = int(m.group("cada_n")), int(m.group("cada_m"))
            if total == 0:
                continue
            numeros.append(Numero(bruto, 100 * n / total, "pct", 50 / total, aproximado))
            continue
        for tipo in ("pp", "pct", "mult", "mil", "tempo", "livre"):
            if m.group(tipo):
                valor, casas = _ler(m.group(f"{tipo}_n") if tipo != "livre" else m.group("livre"))
                tol = 0.5 * 10 ** (-casas)
                if tipo == "mil":
                    escala = 1e3 if m.group("mil_u").lower() == "mil" else 1e6
                    numeros.append(Numero(bruto, valor * escala, "contagem", tol * escala, aproximado))
                elif tipo == "tempo":
                    escala = 60.0 if m.group("tempo_u").lower().startswith("min") else 1.0
                    numeros.append(Numero(bruto, valor * escala, "tempo", tol * escala, aproximado))
                elif tipo == "livre" and casas == 0 and valor <= 10:
                    pass  # inteiros pequenos soltos ("3 etapas") não são resultado
                else:
                    numeros.append(Numero(bruto, valor, tipo, tol, aproximado))
                break
    return numeros


def valores_de(res: ResultadoConsulta) -> dict[str, list[tuple[float, float]]]:
    """Valores citáveis de um resultado, com meia casa da precisão em que são exibidos."""
    v: dict[str, list[tuple[float, float]]] = {"pct": [], "pp": [], "mult": [], "contagem": [], "tempo": []}
    for l in res.linhas:
        if l.suprimido:
            continue
        v["contagem"] += [(l.n, 0.5), (l.casos, 0.5), (l.n_resto, 0.5), (l.casos_resto, 0.5)]
        if l.casos_extrapolados is not None:
            v["contagem"].append((l.casos_extrapolados, 0.5))
        v["pct"].append((100 * l.taxa, 0.05))
        if l.taxa_resto is not None:
            v["pct"].append((100 * l.taxa_resto, 0.05))
            v["pp"].append((100 * (l.taxa - l.taxa_resto), 0.05))
        if l.participacao is not None:
            v["pct"].append((100 * l.participacao, 0.05))
        if l.lift is not None and l.ic95 is not None:
            v["mult"] += [(l.lift, 0.005), (l.ic95[0], 0.005), (l.ic95[1], 0.005)]
        for chave, x in l.extras.items():
            if isinstance(x, (int, float)):
                if chave.endswith("_pct"):
                    v["pct"].append((100 * x, 0.5))
                elif chave.endswith("_s"):
                    v["tempo"].append((float(x), 0.05))
                elif chave.endswith("_min"):
                    v["tempo"].append((60.0 * x, 3.0))
                else:
                    v["contagem"].append((float(x), 0.5))
    for chave, x in res.geral.items():
        if isinstance(x, (int, float)):
            if chave.startswith(("taxa:", "conv:")):
                v["pct"].append((100 * x, 0.05))
            elif chave.endswith("_s"):
                v["tempo"].append((float(x), 0.05))
            else:
                v["contagem"].append((float(x), 0.5))
    return v


def _juntar(varios: list[dict[str, list[tuple[float, float]]]]) -> dict[str, list[tuple[float, float]]]:
    total: dict[str, list[tuple[float, float]]] = {"pct": [], "pp": [], "mult": [], "contagem": [], "tempo": []}
    for v in varios:
        for tipo, lista in v.items():
            total[tipo] += lista
    return total


def casa(numero: Numero, valores: dict[str, list[tuple[float, float]]]) -> bool:
    tipos = ["pct", "pp", "mult", "contagem", "tempo"] if numero.tipo == "livre" else [numero.tipo]
    for tipo in tipos:
        for valor, meia_casa in valores.get(tipo, []):
            tol = numero.tolerancia + meia_casa
            if numero.aproximado:
                tol = max(tol, 0.05 * abs(valor))
            if abs(numero.valor - valor) <= tol:
                return True
    return False


def rotulos_do_registro(registro: RegistroConsultas) -> list[str]:
    rotulos = {l.grupo for r in registro.resultados.values() for l in r.linhas}
    for valores in registro.base.valores.values():
        rotulos.update(valores)
    for r in registro.resultados.values():
        janela = r.params.get("janela_min")
        if janela is not None:
            rotulos.update({f"{janela} min", f"{janela} minutos"})
    return sorted(rotulos)


def _conferir_texto(texto: str, campo: str, citados: set[str], registro: RegistroConsultas, rotulos: list[str],
                    v: Verificacao) -> None:
    citados = citados | set(_QID.findall(texto))
    valores = _juntar([valores_de(r) for q in citados if (r := registro.obter(q)) is not None])
    for numero in extrair_numeros(texto, rotulos):
        if casa(numero, valores):
            v.conferidos.append(f"{numero.texto.strip()} ({campo})")
            continue
        sugestao = next((qid for qid, r in registro.resultados.items() if qid not in citados and casa(numero, valores_de(r))), None)
        dica = f"; esse valor aparece em {sugestao}, que não foi citada" if sugestao else ""
        v.orfaos.append(f"“{numero.texto.strip()}” em {campo} não aparece nas consultas citadas{dica}")


def verificar_candidata(c: Candidata, registro: RegistroConsultas) -> Verificacao:
    v = Verificacao()
    ref = c.evidencia_principal
    res = registro.obter(ref.query_id)
    if res is None:
        v.refs_invalidas.append(f"a evidência principal cita {ref.query_id}, que não existe")
    elif not ref.query_id.endswith("-A"):
        v.refs_invalidas.append(f"a evidência principal precisa ser uma consulta da metade A (…-A), veio {ref.query_id}")
    else:
        metricas = sorted({l.metrica for l in res.linhas})
        linhas = [l for l in res.linhas if l.metrica == ref.metrica]
        if not linhas:
            v.refs_invalidas.append(f"a métrica '{ref.metrica}' não existe em {ref.query_id} (existem: {', '.join(metricas)})")
        elif ref.grupo is not None:
            linha = next((l for l in linhas if l.grupo == ref.grupo), None)
            if linha is None:
                v.refs_invalidas.append(f"o grupo '{ref.grupo}' não aparece em {ref.query_id} para {ref.metrica}")
            elif linha.suprimido:
                v.refs_invalidas.append(f"o grupo '{ref.grupo}' tem menos de 50 usuários (suprimido) e não pode ser citado")
    for qid in c.query_ids_citados:
        if registro.obter(qid) is None:
            v.refs_invalidas.append(f"{qid} foi citado, mas não existe")

    rotulos = rotulos_do_registro(registro)
    citados = {ref.query_id, *c.query_ids_citados}
    for campo in _CAMPOS:
        _conferir_texto(getattr(c, campo), campo, citados, registro, rotulos, v)
    if c.tipo == "padrao_observado" and (m := CAUSAL.search(c.enunciado)):
        v.avisos_causais.append(f"o enunciado traz explicação causal (“{m.group(0)}”); mova para o campo da Research")
    v.verificada = not (v.orfaos or v.refs_invalidas)
    return v


def verificar_textos_gerais(saida: SaidaDevin, registro: RegistroConsultas) -> Verificacao:
    """Resposta ao palpite e lacunas: só valem as consultas citadas no próprio texto."""
    v = Verificacao()
    rotulos = rotulos_do_registro(registro)
    if saida.resposta_ao_palpite:
        _conferir_texto(saida.resposta_ao_palpite, "resposta_ao_palpite", set(), registro, rotulos, v)
    for i, lacuna in enumerate(saida.lacunas, 1):
        _conferir_texto(lacuna, f"lacuna {i}", set(), registro, rotulos, v)
    v.verificada = not v.orfaos
    return v


def problemas_para_agente(saida: SaidaDevin, verificacoes: list[Verificacao], geral: Verificacao) -> list[str]:
    problemas = []
    for c, v in zip(saida.candidatas, verificacoes):
        for p in v.refs_invalidas + v.orfaos + v.avisos_causais:
            problemas.append(f"Candidata “{c.titulo}”: {p}.")
    problemas += [f"{p}." for p in geral.orfaos]
    return problemas


def marcar_sem_fonte(texto: str, orfaos: list[str]) -> str:
    """Marca no texto os números que continuaram sem fonte depois da correção."""
    for orfao in orfaos:
        m = re.match(r"“(.+?)”", orfao)
        if m:
            texto = texto.replace(m.group(1), f"{m.group(1)} ⟦sem fonte⟧", 1)
    return texto
