import math

import pytest

from config import Z_95
from motor.carga import metade_de
from motor.estatistica import destaque, fmt_int, fmt_num, fmt_pct, lift_ic, montar_linhas


def test_lift_e_ic_batem_com_a_conta_na_mao():
    # 15 de 60 no grupo contra 3 de 60 no resto: RR = 0,25 / 0,05 = 5
    rr, lo, hi = lift_ic(15, 60, 3, 60, Z_95)
    ep = math.sqrt(1 / 15 - 1 / 60 + 1 / 3 - 1 / 60)
    assert rr == pytest.approx(5.0)
    assert lo == pytest.approx(math.exp(math.log(5) - Z_95 * ep))
    assert hi == pytest.approx(math.exp(math.log(5) + Z_95 * ep))


def test_contagem_zero_usa_haldane_e_lado_vazio_devolve_none():
    rr, lo, hi = lift_ic(0, 100, 10, 1000, Z_95)
    assert rr == pytest.approx((0.5 / 101) / (10.5 / 1001))
    assert 0 < lo < rr < hi
    assert lift_ic(5, 0, 3, 60, Z_95) is None


def test_montar_linhas_calcula_resto_e_suprime_grupos_pequenos():
    linhas = montar_linhas("dead_click", [("a", 400, 120), ("b", 1600, 40), ("c", 40, 30)], 2040, 190, 10.0)
    a, b, c = linhas
    assert (a.n_resto, a.casos_resto) == (1640, 70)
    assert a.taxa == pytest.approx(0.30) and a.taxa_resto == pytest.approx(70 / 1640)
    assert a.casos_extrapolados == 1200 and a.participacao == pytest.approx(120 / 190)
    assert destaque(a) and not destaque(b)
    assert c.suprimido and not destaque(c)


def test_formatacao_pt_br():
    assert fmt_int(1234567) == "1.234.567"
    assert fmt_num(12.345, 2) == "12,35" and fmt_num(1234.5, 1) == "1.234,5"
    assert fmt_pct(0.1234) == "12,3%" and fmt_pct(None) == "—"


def test_metade_e_deterministica_e_equilibrada():
    ids = [f"usuario-{i}" for i in range(100_000)]
    metades = [metade_de(i) for i in ids]
    assert metades == [metade_de(i) for i in ids]
    assert abs(metades.count("A") / len(ids) - 0.5) < 0.01
