"""
Caracterização: extract_cards_from_html() olx_scraper.py:2012 + ad_id() olx_scraper.py:2007
Fixtures HTML reais salvos (sintético que replica seletor OLX).
"""
import pytest
import olx_scraper as osc


# ---------------------------------------------------------------------------
# ad_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("href,expected", [
    ("https://www.olx.com.br/item/hd-12345.html", "12345"),
    ("https://www.olx.com.br/item/hd-12345/", "12345"),
    ("/item/hd-987.html", "987"),
    ("https://www.olx.com.br/anuncio/pikmin-1507077843.html", "1507077843"),
    ("https://www.olx.com.br/item/foo-999", "999"),  # sem sufixo html/
    ("https://www.olx.com.br/item/sem-id", "https://www.olx.com.br/item/sem-id"),  # fallback = href
    ("", ""),
    ("https://www.olx.com.br/item/hd-42.html?extra=1", "https://www.olx.com.br/item/hd-42.html?extra=1"),  # query não casa
])
def test_ad_id(href, expected):
    assert osc.ad_id(href) == expected


def test_ad_id_numeric_only():
    assert osc.ad_id("https://example.com/-12345.html") == "12345"


# ---------------------------------------------------------------------------
# extract_cards_from_html — HTML sintético
# ---------------------------------------------------------------------------

def _html_with_cards(cards):
    """cards: lista de (href, title, price_text_no_parent) -> html string.
    price_text é colocado no pai para ser encontrado no loop parent 6 níveis.
    """
    inner = ""
    for href, title, price in cards:
        price_html = f"R$ {price}" if price is not None else ""
        # estrutura: <div><div><a ...>title</a> price</div></div> — price no parent
        inner += f'<div><a data-testid="adcard-link" href="{href}">{title}</a> {price_html}</div>'
    return f"<html><body>{inner}</body></html>"


def test_extract_cards_basic_two_cards():
    html = _html_with_cards([
        ("https://www.olx.com.br/item/hd-111.html", "HD 8TB Seagate", "800"),
        ("https://www.olx.com.br/item/hd-222.html", "HD 10TB WD", "900,00"),
    ])
    cards = osc.extract_cards_from_html(html)
    assert len(cards) == 2
    assert cards[0]["id"] == "111"
    assert cards[0]["title"] == "HD 8TB Seagate"
    assert cards[0]["price"] == pytest.approx(800.0)
    assert cards[0]["url"] == "https://www.olx.com.br/item/hd-111.html"
    assert cards[1]["price"] == pytest.approx(900.0)


def test_extract_cards_relative_url_prefix():
    html = _html_with_cards([
        ("/item/hd-333.html", "HD Test", "750"),
    ])
    cards = osc.extract_cards_from_html(html)
    assert cards[0]["url"] == "https://www.olx.com.br/item/hd-333.html"
    assert cards[0]["id"] == "333"


def test_extract_cards_no_price_yields_none():
    html = _html_with_cards([
        ("https://www.olx.com.br/item/hd-444.html", "HD Sem Preco", None),
    ])
    cards = osc.extract_cards_from_html(html)
    assert len(cards) == 1
    assert cards[0]["price"] is None
    assert cards[0]["title"] == "HD Sem Preco"


def test_extract_cards_empty_title_skipped():
    html = '<html><body><div><a data-testid="adcard-link" href="https://www.olx.com.br/item/hd-555.html"></a> R$ 100</div></body></html>'
    cards = osc.extract_cards_from_html(html)
    assert cards == []


def test_extract_cards_ignores_non_adcard_links():
    html = '<html><body><a href="https://www.olx.com.br/item/hd-999.html">HD 8TB</a> R$ 800</body></html>'
    cards = osc.extract_cards_from_html(html)
    assert cards == []


def test_extract_cards_price_in_ancestor():
    # preço não no anchor, mas em ancestral até 6 níveis
    html = '''
    <html><body>
      <div>R$ 1.200,50
        <div><div><div><div><div>
          <a data-testid="adcard-link" href="https://www.olx.com.br/item/hd-666.html">HD Deep</a>
        </div></div></div></div></div>
      </div>
    </body></html>
    '''
    cards = osc.extract_cards_from_html(html)
    assert len(cards) == 1
    assert cards[0]["price"] == pytest.approx(1200.50)


def test_extract_cards_no_cards_returns_empty():
    html = "<html><body><p>sem anuncios</p></body></html>"
    assert osc.extract_cards_from_html(html) == []


def test_extract_cards_price_brazilian_format():
    html = _html_with_cards([
        ("https://www.olx.com.br/item/hd-777.html", "HD 6TB", "1.200,50"),
    ])
    cards = osc.extract_cards_from_html(html)
    assert cards[0]["price"] == pytest.approx(1200.50)
