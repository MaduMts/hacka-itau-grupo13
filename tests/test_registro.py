from registro import Registro, novo_run_id


def test_registro_e_append_only_em_utf8(tmp_path):
    run_id = novo_run_id()
    reg = Registro(run_id, pasta=tmp_path)
    reg.evento("sistema", "execucao_iniciada", squad="cartoes")
    reg.evento("pm", "decisao", acao="aceitar", comentario="Ação: priorizar correção na confirmação")

    eventos = reg.ler()
    assert [e["seq"] for e in eventos] == [1, 2]
    assert eventos[1]["ator"] == "pm" and eventos[1]["dados"]["comentario"].startswith("Ação")
    assert "ç" in (tmp_path / f"{run_id}.jsonl").read_text(encoding="utf-8")

    # Reabrir continua a numeração, sem sobrescrever
    reg2 = Registro(run_id, pasta=tmp_path)
    reg2.evento("agente", "rodada_publicada", rodada=1)
    assert [e["seq"] for e in reg2.ler()] == [1, 2, 3]
    assert not any(hasattr(reg2, nome) for nome in ("apagar", "remover", "limpar"))
