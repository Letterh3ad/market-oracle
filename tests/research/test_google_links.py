import base64
import json

from oracle.research.google_links import decode_offline, decoded_url, decoder_form, signature, stub_id

# Captured from the live feed on 2026-09-24: every stub there now is the newer, opaque kind.
BBC_ID = ("CBMiWkFVX3lxTFB4Tk9KM0RQSVhlNjVDMWlJWUQ4YmxpQlZ0NFdXMF9qZEpPSnd6OGVBeHJmYWNXTkw4SXF2djM4MlcyX3hIenow"
          "bC1WRkpVMkV4WEFNQW1OOV9vZw")
FT_ID = ("CBMihAFBVV95cUxOaVk5NE9nSUZPeUVKVGl6MUR4emVOZUdlb2x5dGctTi1aODFZaGxqWWlSY2tLQXBhVDNjdVJrOHE0VlRNbW9H"
         "VGpodkNhaEMyOVFsaHI5OUdka2VUUjI4X2FpWUwxUGZNWUQwV3pVYlFjRXBfdFd2TWVrWDFwUjhaMkVZT2s")
# The stub page's signature element and the decoder's answer for BBC_ID, as captured.
BBC_PAGE = (f'<div jscontroller="aLI87" jsaction="rcuQ6b:npT2md" data-n-a-id="{BBC_ID}" data-n-a-ts="1790261271" '
            'data-n-a-sg="AbIaSL9FWHyBHNJxT2DaghkfeJ4Z" data-n-dnlg="false"></div>')
BBC_ANSWER = (')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.bbc.com/news/articles/cp87m46g392o\\",1]",'
              'null,null,null,"generic"],["di",10],["af.httprm",10,"-5081948287694909850",8]]')


def old_style(url: str, amp: str = "") -> str:
    """An id of the older kind, laid out as Google issued them: the address, then an optional AMP copy."""
    def field(tag: bytes, value: str) -> bytes:
        body = value.encode()
        n, length = len(body), b""
        while n > 0x7F:
            length += bytes([n & 0x7F | 0x80])
            n >>= 7
        return tag + length + bytes([n]) + body
    raw = b"\x08\x13" + field(b"\x22", url) + field(b"\xd2\x01", amp)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_a_stub_id_comes_from_either_stub_path_and_nothing_else():
    assert stub_id(f"https://news.google.com/rss/articles/{BBC_ID}?oc=5") == BBC_ID
    assert stub_id(f"https://news.google.com/articles/{BBC_ID}") == BBC_ID
    assert stub_id(f"https://news.google.com.evil.test/rss/articles/{BBC_ID}") is None
    assert stub_id("https://news.google.com/rss/search?q=x") is None
    assert stub_id("https://news.google.com/rss/articles/bad%2Fid") is None


def test_an_older_id_carries_its_address_and_decodes_offline():
    url = "https://www.reuters.com/markets/us/apple-beats-estimates-2021-01-27/"
    assert old_style(url).startswith("CBMi")
    assert decode_offline(old_style(url)) == url
    assert decode_offline(old_style(url, "https://www.reuters.com/amp/x")) == url
    long = "https://www.example.com/" + "a" * 300
    assert decode_offline(old_style(long)) == long


def test_a_newer_id_needs_googles_decoder():
    assert decode_offline(BBC_ID) is None and decode_offline(FT_ID) is None


def test_ids_that_are_not_an_address_decode_to_nothing():
    assert decode_offline("CBMiabc") is None
    assert decode_offline(old_style("javascript:alert(1)")) is None
    assert decode_offline(old_style("https://x.test/")[:-6]) is None
    assert decode_offline("!!!") is None


def test_the_signature_is_read_from_the_stub_page():
    assert signature(BBC_PAGE) == (1790261271, "AbIaSL9FWHyBHNJxT2DaghkfeJ4Z")
    assert signature("<html>consent</html>") is None


def test_the_decoder_form_carries_the_id_and_signature():
    form = decoder_form(BBC_ID, 1790261271, "sig")
    [[[rpc, inner, _, _]]] = json.loads(form["f.req"])
    request = json.loads(inner)
    assert rpc == "Fbv4je" and request[0] == "garturlreq" and request[2:] == [BBC_ID, 1790261271, "sig"]


def test_the_decoders_answer_gives_the_address():
    assert decoded_url(BBC_ANSWER) == "https://www.bbc.com/news/articles/cp87m46g392o"
    assert decoded_url(')]}\'\n\n[["er",null,null,null,null,405]]') is None
    assert decoded_url('[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"ftp://x\\",1]"]]') is None
    assert decoded_url('[["wrb.fr","Fbv4je","not json"]]') is None
