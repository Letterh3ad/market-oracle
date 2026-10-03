import pytest

from oracle.lab.matching import keyword_score, normal_title, source_key, title_score


def test_keyword_score_counts_shared_content_words_across_plurals_and_stop_words():
    # {buy, stock, near, yearly, high} against {stock, near, 52, week, high, keep, rising}
    assert keyword_score("buy stocks near their yearly high", "stocks near their 52-week high keep rising") == 0.5
    assert keyword_score("Momentum", "momentum") == 1.0
    assert keyword_score("the of", "momentum") == 0.0
    assert keyword_score("value stocks", "") == 0.0


@pytest.mark.parametrize("text", [
    "https://doi.org/10.1111/j.1540-6261.1992.tb04398.x",
    "http://dx.doi.org/10.1111/J.1540-6261.1992.TB04398.X",
    "doi:10.1111/j.1540-6261.1992.tb04398.x",
    "10.1111/j.1540-6261.1992.tb04398.x.",
    "https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04398.x?utm_source=x",
    "https://doi.org/10.1111/j.1540-6261.1992.tb04398.x/",
    "http://dx.doi.org/10.1111/j.1540-6261.1992.tb04398.x/",
    "https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1992.tb04398.x/",
    "https://onlinelibrary.wiley.com/doi/full/10.1111/j.1540-6261.1992.tb04398.x",
    "https://onlinelibrary.wiley.com/doi/pdf/10.1111/j.1540-6261.1992.tb04398.x",
    "https://onlinelibrary.wiley.com/doi/epdf/10.1111/j.1540-6261.1992.tb04398.x",
    "https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04398.x/full",
    "https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04398.x/abstract",
])
def test_doi_variants_are_one_key(text):
    assert source_key(text) == "doi:10.1111/j.1540-6261.1992.tb04398.x"


def test_a_doi_with_brackets_keeps_them():
    assert source_key("https://doi.org/10.1016/0304-405X(95)00826-Z") == "doi:10.1016/0304-405x(95)00826-z"


@pytest.mark.parametrize("text", [
    "https://papers.ssrn.com/abstract=2312432",
    "http://papers.ssrn.com/sol3/papers.cfm?abstract_id=2312432&download=yes",
    "SSRN 2312432",
    "ssrn:2312432",
    "https://doi.org/10.2139/ssrn.2312432",
])
def test_ssrn_variants_are_one_key(text):
    assert source_key(text) == "ssrn:2312432"


@pytest.mark.parametrize("text", [
    "https://arxiv.org/abs/2304.07619",
    "http://arxiv.org/pdf/2304.07619v6",
    "https://arxiv.org/abs/2304.07619v2/",
    "arXiv:2304.07619",
    "arXiv 2304.07619 v6",
    "https://doi.org/10.48550/arXiv.2304.07619",
])
def test_arxiv_variants_are_one_key(text):
    assert source_key(text) == "arxiv:2304.07619"


@pytest.mark.parametrize("text", [
    "https://www.nber.org/papers/w13739",
    "http://nber.org/papers/w13739/",
    "https://www.nber.org/system/files/working_papers/w13739/w13739.pdf",
])
def test_nber_variants_are_one_key(text):
    assert source_key(text) == "nber:w13739"


@pytest.mark.parametrize("text", [
    "https://www.aqr.com/Insights/Research/Paper",
    "http://aqr.com/Insights/Research/Paper/",
    "https://WWW.AQR.COM/Insights/Research/Paper?utm_source=news&utm_medium=email#top",
    "https://www.aqr.com/Insights/Research/Paper?fbclid=abc&gclid=x",
    "www.aqr.com/Insights/Research/Paper",
])
def test_url_variants_are_one_key(text):
    assert source_key(text) == "url:aqr.com/Insights/Research/Paper"


def test_a_meaningful_query_is_kept_and_sorted():
    assert source_key("https://example.com/p?b=2&utm_campaign=z&a=1") == "url:example.com/p?a=1&b=2"


@pytest.mark.parametrize("text", ["Lazy Prices", "momentum 12-1", "", "   ", "the 52-week high"])
def test_plain_text_is_not_an_id(text):
    assert source_key(text) is None


def test_titles_normalise_case_accents_and_punctuation():
    assert normal_title("  López de Prado: Advances—in Financial ML!  ") == "lopez de prado advances in financial ml"


def test_a_mangled_title_still_scores_high_and_an_unrelated_one_low():
    real = "Does Academic Research Destroy Stock Return Predictability?"
    assert title_score("does academic reserch destory stock-return predictibility", real) > 0.85
    assert title_score("Betting Against Beta", real) < 0.45


def test_a_short_query_matches_a_longer_name_it_is_part_of():
    assert title_score("52 week high", "52-week-high proximity") > 0.7
    assert title_score("52 week high", "52-week-high proximity") > title_score("52 week high", "Lazy Prices")


def test_empty_strings_score_zero():
    assert title_score("", "Lazy Prices") == 0.0
    assert title_score("Lazy Prices", "") == 0.0


@pytest.mark.parametrize("text", [
    "https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04398.x/abstract;jsessionid=ABC123",
    "https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04398.x;jsessionid=ABC123",
    "https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1992.tb04398.x;jsessionid=A1/",
])
def test_a_session_path_param_is_not_part_of_the_doi(text):
    assert source_key(text) == "doi:10.1111/j.1540-6261.1992.tb04398.x"


def test_a_sici_doi_keeps_its_semicolon():
    assert (source_key("https://doi.org/10.1002/(SICI)1097-4571(199806)49:8<693::AID-ASI4>3.0.CO;2-O")
            == "doi:10.1002/(sici)1097-4571(199806)49:8<693::aid-asi4>3.0.co;2-o")
