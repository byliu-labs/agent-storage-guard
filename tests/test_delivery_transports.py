"""Delivery acceptance is exercised at the network and native process seams."""
import pytest
from agent_storage_guard import delivery_transports as delivery


def test_feishu_rejects_untrusted_endpoint_without_exposing_it():
    with pytest.raises(delivery.DeliveryError) as error:
        delivery.check("feishu", {"webhook_url": "https://private.invalid/secret"})
    assert str(error.value) == "invalid_configuration"
    assert error.value.retryable is False

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SETTINGS = {"feishu": {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/synthetic"}}


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.server.requests.append((self.path, self.headers, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(self.server.status)
            self.send_header("Retry-After", self.server.retry_after)
            self.send_header("Location", "http://127.0.0.1:1/private")
            if getattr(self.server, "truncated", False):
                self.send_header("Content-Length", str(len(self.server.body) + 100))
            self.end_headers()
            if getattr(self.server, "drip", False):
                import time
                try:
                    for byte in self.server.body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.wfile.write(self.server.body)
        def log_message(self, *args):
            pass
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    instance.requests = []
    instance.status = 200
    instance.retry_after = "7200"
    instance.body = b"ok"
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def route(monkeypatch, server):
    original = delivery._request
    def local(url, payload, headers):
        return original(f"http://127.0.0.1:{server.server_port}/", payload, headers)
    monkeypatch.setattr(delivery, "_request", local)


@pytest.mark.parametrize("provider,body", [("feishu", b'{"code":0}'), ("feishu", b'{"StatusCode":0}')])
def test_provider_acceptance_through_real_http(monkeypatch, server, provider, body):
    route(monkeypatch, server)
    server.body = body
    delivery.send(provider, SETTINGS[provider], "Synthetic capacity alert")
    assert len(server.requests) == 1
    assert server.requests[0][2]["content"] == {"text": "Synthetic capacity alert"}


@pytest.mark.parametrize("provider", SETTINGS)
@pytest.mark.parametrize("status,body,code,retryable", [(200,b'{"error":"private-secret"}',"provider_rejected",False),(429,b'private-secret',"rate_limited",True),(503,b'private-secret',"provider_unavailable",True),(302,b'private-secret',"redirect_rejected",False),(200,b'not-json',"provider_rejected",False)])
def test_http_failures_are_redacted(monkeypatch, server, provider, status, body, code, retryable):
    route(monkeypatch, server)
    server.status, server.body = status, body
    with pytest.raises(delivery.DeliveryError) as error:
        delivery.send(provider, SETTINGS[provider], "Synthetic alert")
    assert str(error.value) == code
    assert error.value.retryable is retryable
    assert error.value.retry_after == (3600 if status == 429 else 0)
    assert len(server.requests) == 1


def test_feishu_signing_and_lark_route(monkeypatch, server):
    route(monkeypatch, server)
    monkeypatch.setattr(delivery.time, "time", lambda: 1600000000)
    server.body = b'{"code":0}'
    settings = {"webhook_url": "https://open.larksuite.com/open-apis/bot/v2/hook/synthetic", "signing_secret": "synthetic-signing-secret"}
    delivery.send("feishu", settings, "Synthetic alert")
    payload = server.requests[0][2]
    assert payload["timestamp"] == "1600000000"
    assert payload["sign"] == "ASAOsx6TxpRlohmEqW/U7EqEVae9EygmYjphMX7yHno="


@pytest.mark.parametrize("url", [None, 42, "http://open.feishu.cn/open-apis/bot/v2/hook/secret", "https://open.feishu.cn.evil.invalid/open-apis/bot/v2/hook/secret", "https://private:secret@open.feishu.cn/open-apis/bot/v2/hook/token", "https://open.feishu.cn:bad/open-apis/bot/v2/hook/token", "https://open.feishu.cn/open-apis/bot/v2/hook/", "https://open.feishu.cn/open-apis/bot/v2/hook/token?secret=yes"])
def test_malformed_settings_never_leak_details(url):
    with pytest.raises(delivery.DeliveryError, match="^invalid_configuration$"):
        delivery.check("feishu", {"webhook_url": url})


@pytest.mark.parametrize("body", [b'{"code":false}', b'{"code":"0"}', b'[]', b'null', b'{"code":1}', b'x'*65537])
def test_nonacceptance_and_oversized_response(monkeypatch, server, body):
    route(monkeypatch, server)
    server.body = body
    with pytest.raises(delivery.DeliveryError, match="^provider_rejected$"):
        delivery.send("feishu", SETTINGS["feishu"], "Synthetic alert")


@pytest.mark.parametrize("exception,code", [(TimeoutError("secret"), "timeout"), (OSError("private endpoint"), "network_failure")])
def test_network_failures_and_timeouts_are_bounded_and_redacted(monkeypatch, exception, code):
    class BrokenNetwork:
        def open(self, req, timeout):
            assert timeout == 5
            raise exception
    monkeypatch.setattr(delivery.request, "build_opener", lambda *args: BrokenNetwork())
    with pytest.raises(delivery.DeliveryError) as failure:
        delivery._http_request(SETTINGS["feishu"]["webhook_url"], {"text": "Synthetic alert"}, {})
    assert str(failure.value) == code
    assert failure.value.retryable is True


def test_health_check_does_not_contact_network(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Health check attempted network")
    monkeypatch.setattr(delivery.request, "build_opener", forbidden)
    delivery.check("feishu", SETTINGS["feishu"])


@pytest.mark.parametrize("delay,expected", [("-1",0), ("nonsense",0), ("nan",0), ("30",30)])
def test_rate_limit_delay_is_bounded(monkeypatch, server, delay, expected):
    route(monkeypatch, server)
    server.status, server.retry_after = 429, delay
    with pytest.raises(delivery.DeliveryError) as failure:
        delivery.send("feishu", SETTINGS["feishu"], "Synthetic alert")
    assert failure.value.retry_after == expected


def test_truncated_http_response_is_a_redacted_retryable_failure(monkeypatch, server):
    route(monkeypatch, server)
    server.body = b'{"code":0}'
    server.truncated = True
    with pytest.raises(delivery.DeliveryError, match="^network_failure$") as failure:
        delivery.send("feishu", SETTINGS["feishu"], "Synthetic alert")
    assert failure.value.retryable is True


@pytest.mark.parametrize("url", ["\x00https://open.feishu.cn/open-apis/bot/v2/hook/private", "https://open.feishu.cn/open-apis/bot/v2/hook/pr\x01ivate", "https://open.feishu.cn/open-apis/bot/v2/hook/\ud800"])
def test_control_and_surrogate_urls_fail_locally(url):
    with pytest.raises(delivery.DeliveryError, match="^invalid_configuration$"):
        delivery.check("feishu", {"webhook_url": url})


def test_surrogate_message_is_redacted():
    with pytest.raises(delivery.DeliveryError, match="^invalid_payload$"):
        delivery.send("feishu", SETTINGS["feishu"], "\ud800")


def test_surrogate_signing_secret_fails_locally():
    with pytest.raises(delivery.DeliveryError, match="^invalid_configuration$"):
        delivery.check("feishu", {**SETTINGS["feishu"], "signing_secret": "\ud800"})


@pytest.mark.parametrize("body", [b'{"code":0,"StatusCode":1}', b'['*1100+b']'*1100])
def test_conflicting_or_recursive_response_is_rejected(monkeypatch, server, body):
    route(monkeypatch, server)
    server.body = body
    with pytest.raises(delivery.DeliveryError, match="^provider_rejected$"):
        delivery.send("feishu", SETTINGS["feishu"], "Synthetic alert")


def test_slow_drip_has_absolute_deadline_and_reaps_worker(monkeypatch, server):
    import time
    import subprocess
    route(monkeypatch, server)
    server.body = b'{"code":0}'
    server.drip = True
    monkeypatch.setattr(delivery, "_DEADLINE_SECONDS", .25)
    children = []
    original = subprocess.Popen
    class ObservedChild(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            children.append(self)
    monkeypatch.setattr(subprocess, "Popen", ObservedChild)
    started = time.monotonic()
    with pytest.raises(delivery.DeliveryError, match="^timeout$"):
        delivery.send("feishu", SETTINGS["feishu"], "Synthetic alert")
    assert time.monotonic() - started < 1.5
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("stdout,returncode", [("private diagnostic",0), ('{"code":"private diagnostic"}',0), ('{"data":"invalid!!"}',0), ('{"code":"timeout","retry_after":"private"}',0), ('{}',1)])
def test_worker_failures_cannot_escape_into_diagnostics(monkeypatch, stdout, returncode):
    import subprocess
    def finished(args, **kwargs):
        assert args == [delivery.sys.executable, "-m", "agent_storage_guard.delivery_transports"]
        assert kwargs["timeout"] == 8
        assert "synthetic" not in " ".join(args)
        return subprocess.CompletedProcess(args, returncode, stdout, "private stderr")
    monkeypatch.setattr(subprocess, "run", finished)
    with pytest.raises(delivery.DeliveryError, match="^network_failure$"):
        delivery.send("feishu", SETTINGS["feishu"], "Synthetic alert")
