import pytest

from services import item_catalog
from services._paths import bundled


@pytest.mark.skipif(not bundled("tthol.sqlite").exists(), reason="tthol.sqlite not pulled")
def test_search_by_name_and_leave_out_untradable():
    hits = item_catalog.search("醉月劍法")
    assert hits and all("醉月劍法" in h.name for h in hits)
    assert [len(h.name) for h in hits] == sorted(len(h.name) for h in hits)
    assert not any(h.no_trade for h in item_catalog.search("寶匣", 100, tradable=True))
    assert item_catalog.search("  ") == []


@pytest.mark.skipif(not bundled("tthol.sqlite").exists(), reason="tthol.sqlite not pulled")
def test_search_by_category_alone():
    books = item_catalog.search("", 1000, tradable=True, category="book")
    assert len(books) > 100 and all(b.category == "book" for b in books)
    blades = item_catalog.search("刀", 1000, category="book")
    assert blades and all("刀" in b.name for b in blades)
