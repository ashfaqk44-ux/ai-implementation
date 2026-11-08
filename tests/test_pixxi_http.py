import httpx
import pytest

from src.config import Settings
from src.pixxi.http import TOKEN_HEADER, PixxiError, PixxiHttp


def make(handler, **overrides):
    settings = Settings(_env_file=None, pixxi_token="tok", pixxi_base_url="http://x", **overrides)
    client = httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler))
    slept: list[float] = []
    clock = {"t": 100.0}
    http = PixxiHttp(settings, client=client, clock=lambda: clock["t"], sleep=lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)))
    return http, slept, clock


def test_unwraps_envelope_and_sends_token():
    seen = {}

    def handler(request):
        seen["token"] = request.headers.get(TOKEN_HEADER)
        return httpx.Response(200, json={"statusCode": 200, "message": "success", "data": {"a": 1}})

    http, _, _ = make(handler, pixxi_min_read_interval=0)
    assert http.request("POST", "/p", kind="read", json={}) == {"a": 1}
    assert seen["token"] == "tok"


def test_error_in_envelope_raises():
    http, _, _ = make(lambda r: httpx.Response(200, json={"statusCode": 400, "message": "bad", "data": None}),
                      pixxi_min_read_interval=0)
    with pytest.raises(PixxiError) as exc:
        http.request("POST", "/p", kind="read")
    assert exc.value.status_code == 400


def test_http_error_raises():
    http, _, _ = make(lambda r: httpx.Response(500, text="boom"), pixxi_min_read_interval=0)
    with pytest.raises(PixxiError) as exc:
        http.request("GET", "/p", kind="read")
    assert exc.value.status_code == 500


def test_retries_429_then_succeeds():
    responses = iter([httpx.Response(429), httpx.Response(429),
                      httpx.Response(200, json={"statusCode": 200, "data": "ok"})])
    http, slept, _ = make(lambda r: next(responses), pixxi_min_read_interval=0)
    assert http.request("GET", "/p", kind="read") == "ok"
    assert len(slept) == 2


def test_gives_up_after_max_429_retries():
    http, _, _ = make(lambda r: httpx.Response(429), pixxi_min_read_interval=0, pixxi_max_retries_on_429=2)
    with pytest.raises(PixxiError) as exc:
        http.request("GET", "/p", kind="read")
    assert exc.value.status_code == 429


def test_reads_are_spaced_but_writes_are_not():
    ok = lambda r: httpx.Response(200, json={"statusCode": 200, "data": None})
    http, slept, _ = make(ok, pixxi_min_read_interval=2.0)
    http.request("POST", "/r", kind="read")
    http.request("POST", "/r", kind="read")
    assert slept == [2.0]
    http.request("POST", "/w", kind="write")
    assert slept == [2.0]


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_forbidden_methods_are_refused(method):
    http, _, _ = make(lambda r: httpx.Response(200), pixxi_min_read_interval=0)
    with pytest.raises(ValueError):
        http.request(method, "/pixxiapi/v1/lead/update/App", kind="write")
    assert http.calls == []
