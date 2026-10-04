"""Unit tests — no network. The traps these cover are all real incidents."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from comeet import mint_token
from comeet.auth import _parse_dotenv_line
from comeet.client import _is_bandwidth_response, _is_transient_status
from comeet.resolve import is_api_uid, numeric_id


def test_numeric_id_from_raw_and_url():
    assert numeric_id("60235428") == "60235428"
    assert numeric_id("https://app.comeet.co/app/?goto_url=/can/60235428") == "60235428"
    assert numeric_id("AE.17934") is None


def test_api_uid_recognised():
    assert is_api_uid("AE.17934")
    assert is_api_uid("B4.B5F21")
    assert not is_api_uid("60235428")


def test_bandwidth_detected_by_body_not_only_status():
    # Comeet answers 200 with a quota message in the body on some paths.
    assert _is_bandwidth_response(429, "")
    assert _is_bandwidth_response(200, "Bandwidth quota exceeded")
    assert _is_bandwidth_response(200, "rate of data transfer")
    assert not _is_bandwidth_response(200, "ok")


def test_302_is_transient():
    # A 302 on an API path means the gateway bounced us, not a real redirect.
    assert _is_transient_status(302)
    assert _is_transient_status(503)
    assert not _is_transient_status(404)


def test_dotenv_handles_export_quotes_and_comments():
    assert _parse_dotenv_line('export COMEET_API_KEY="abc" # note') == ("COMEET_API_KEY", "abc")
    assert _parse_dotenv_line("COMEET_API_SECRET=s3cret") == ("COMEET_API_SECRET", "s3cret")
    assert _parse_dotenv_line("# comment") is None
    assert _parse_dotenv_line("") is None


def test_token_is_hs256_with_key_as_issuer():
    import jwt
    tok = mint_token("KEY", "SECRET", ttl_seconds=60)
    claims = jwt.decode(tok, "SECRET", algorithms=["HS256"])
    assert claims["iss"] == "KEY"
