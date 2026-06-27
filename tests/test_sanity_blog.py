"""I blogPost di Sanity sono bilingui (titleIt/bodyIt + titleEn/bodyEn). I clienti
scrivono in italiano: indicizzare solo l'inglese (com'era) lascia fuori il contenuto
nella lingua dei clienti. Il documento deve includere l'italiano (e l'inglese per
recall cross-lingua)."""
from sync.sanity_sync import _build_blog_document


def _pt(text):
    return [{"_type": "block", "children": [{"_type": "span", "text": text}]}]


def test_italian_title_and_body_indexed():
    post = {
        "_id": "p1",
        "titleIt": "Come arrivare a Gate Sardinia da Olbia",
        "bodyIt": _pt("In auto da Olbia segui la SS125 per circa 40 minuti."),
        "titleEn": "How to Reach Gate Sardinia from Olbia",
        "bodyEn": _pt("By car from Olbia follow the SS125 for about 40 minutes."),
    }
    doc, _ = _build_blog_document(post, "Gate Sardinia")
    assert "Come arrivare a Gate Sardinia da Olbia" in doc
    assert "SS125 per circa 40 minuti" in doc


def test_both_languages_present_for_cross_lingual_recall():
    post = {
        "_id": "p1",
        "titleIt": "Le tre sale", "bodyIt": _pt("2500 metri quadri e tre sale a Budoni."),
        "titleEn": "The Space", "bodyEn": _pt("2,500 square metres and three rooms in Budoni."),
    }
    doc, _ = _build_blog_document(post, "Gate Sardinia")
    assert "tre sale a Budoni" in doc       # italiano
    assert "three rooms in Budoni" in doc   # inglese


def test_falls_back_to_english_when_italian_absent():
    post = {"_id": "p1", "titleEn": "English Only", "bodyEn": _pt("Only english body here.")}
    doc, _ = _build_blog_document(post, "Gate Sardinia")
    assert "English Only" in doc
    assert "Only english body here" in doc
