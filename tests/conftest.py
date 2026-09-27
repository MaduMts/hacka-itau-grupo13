"""Configuração comum dos testes.

Os testes nunca falam com o Devin: a chave é esvaziada antes de qualquer import do projeto
(o load_dotenv não sobrescreve variáveis que já existem no ambiente). Os registros de execução
dos testes vão para uma pasta temporária, fora do projeto.
"""
import os
import tempfile

os.environ["DEVIN_API_KEY"] = ""
os.environ["PASTA_REGISTROS"] = tempfile.mkdtemp(prefix="registros_teste_")

import pytest  # noqa: E402

from gerador.gerar_dados import garantir_exemplo  # noqa: E402


@pytest.fixture(scope="session")
def pasta_normal():
    return garantir_exemplo("normal")


@pytest.fixture(scope="session")
def pasta_incompleta():
    return garantir_exemplo("incompleta")
