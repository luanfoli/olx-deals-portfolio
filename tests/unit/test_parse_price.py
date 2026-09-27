"""
Caracterização: parse_price() olx_scraper.py:1986

Cobre heurística brasileira: 1.200,50 / 1.200 (milhar) / 750.50 (decimal US)
Sem tocar produção.
"""
import pytest
import olx_scraper as osc


@pytest.mark.parametrize("raw,expected", [
    # formato brasileiro com vírgula decimal
    ("R$ 1.200,50", 1200.50),
    ("R$ 1.200,50", 1200.50),
    ("R$ 10.000,00", 10000.0),
    ("R$ 1.000,00", 1000.0),
    ("R$ 999,99", 999.99),
    ("R$ 1.234.567,89", 1234567.89),
    # sem vírgula, milhar (último grupo 3 dígitos)
    ("R$ 1.200", 1200.0),
    ("R$ 1.000", 1000.0),
    ("R$ 12.000", 12000.0),
    # sem vírgula, decimal US (2 dígitos)
    ("R$ 750.50", 750.50),
    ("R$ 100.99", 100.99),
    # inteiro simples
    ("R$ 750", 750.0),
    ("R$ 900", 900.0),
    ("R$  750  ", 750.0),
    ("R$1.200,50", 1200.50),  # sem espaço
    # múltiplos pontos (milhar)
    ("R$ 1.200.500", 1200500.0),
    ("R$ 1.200.300", 1200300.0),
    # dentro de texto maior (primeiro R$ casa)
    ("Anúncio R$ 1.200,50 frete", 1200.50),
    ("  R$ 900  ", 900.0),
])
def test_parse_price_brazilian_heuristics(raw, expected):
    assert osc.parse_price(raw) == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("txt", [
    "",
    "sem preço",
    "R$ ",
    "R$ abc",
    "US$ 100",
    "1000",
    "Gratis",
])
def test_parse_price_none_when_no_match(txt):
    assert osc.parse_price(txt) is None


def test_parse_price_none_on_invalid_format():
    # PRICE_RE exige R$ prefixo
    assert osc.parse_price("1.200,50") is None


def test_parse_price_first_match_wins():
    # duas ocorrências, primeira vence
    assert osc.parse_price("R$ 100 R$ 200") == pytest.approx(100.0)


def test_parse_price_preserves_none_for_price_regression_issue_8():
    # garante que parse_price não lança e retorna None para string vazia
    assert osc.parse_price("R$ ") is None


def test_parse_price_edge_milhar_vs_decimal():
    # 1.200 tem 3 dígitos no último grupo -> milhar
    assert osc.parse_price("R$ 1.200") == pytest.approx(1200.0)
    # 1.20 tem 2 dígitos -> decimal
    assert osc.parse_price("R$ 1.20") == pytest.approx(1.20)
    # 12.340 -> 3 dígitos -> milhar (comportamento atual, mesmo sendo ambíguo)
    assert osc.parse_price("R$ 12.340") == pytest.approx(12340.0)
