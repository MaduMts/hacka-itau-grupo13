import json

import pytest
from pydantic import ValidationError

from contratos import SaidaDevin, schema_devin, tamanho_schema_bytes


def _objetos(no):
    if isinstance(no, dict):
        if no.get("type") == "object":
            yield no
        for v in no.values():
            yield from _objetos(v)
    elif isinstance(no, list):
        for item in no:
            yield from _objetos(item)


def test_schema_devin_e_draft7_autocontido_e_pequeno():
    schema = schema_devin()
    texto = json.dumps(schema)
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert "$ref" not in texto and "$defs" not in texto
    assert tamanho_schema_bytes(schema) < 64 * 1024
    for obj in _objetos(schema):
        assert obj.get("additionalProperties") is False
        assert set(obj["required"]) == set(obj["properties"])


def test_schema_sem_restricoes_numericas_ou_de_tamanho():
    texto = json.dumps(schema_devin())
    for proibido in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "multipleOf"):
        assert proibido not in texto


PLANO = {
    "rodada": 1,
    "fase": "plano",
    "consultas": [
        {"ferramenta": "atrito", "segmentar_por": "versao_app", "filtros": [], "pagina": "confirmacao_bloqueio",
         "tipo_atrito": "dead_click", "evento_a": None, "evento_b": None, "janela_min": None,
         "motivo": "Testar o palpite sobre a confirmação"},
    ],
    "candidatas": [],
    "resposta_ao_palpite": None,
    "lacunas": [],
}


def test_saida_devin_valida_plano_e_candidatas():
    assert SaidaDevin.model_validate(PLANO).consultas[0].ferramenta == "atrito"
    candidatas = {
        **PLANO,
        "rodada": 2,
        "fase": "candidatas",
        "consultas": [],
        "candidatas": [{
            "tipo": "padrao_observado", "titulo": "Confirmação não responde no Android 8.4.0",
            "enunciado": "Usuários do android 8.4.0 dão mais dead click no botão de confirmar.",
            "evidencia_principal": {"query_id": "Q01-A", "grupo": "android 8.4.0", "metrica": "dead_click"},
            "query_ids_citados": ["Q01-A"], "o_que_o_dado_responde": "Quem e quanto.",
            "o_que_so_a_research_responde": "Por que o botão não responde.", "pergunta_para_research": "O que o cliente vê?",
        }],
        "resposta_ao_palpite": "Parcialmente confirmado.",
    }
    assert SaidaDevin.model_validate(candidatas).candidatas[0].evidencia_principal.grupo == "android 8.4.0"


def test_saida_devin_rejeita_campo_extra_e_ferramenta_fora_do_catalogo():
    with pytest.raises(ValidationError):
        SaidaDevin.model_validate({**PLANO, "sql_livre": "select *"})
    ruim = json.loads(json.dumps(PLANO))
    ruim["consultas"][0]["ferramenta"] = "sql"
    with pytest.raises(ValidationError):
        SaidaDevin.model_validate(ruim)
