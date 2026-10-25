from ui.api_client import ZenithAPI, _fast_loopback


def test_localhost_is_rewritten_to_ipv4_loopback():
    # Windows resolves "localhost" to ::1 first; an IPv4-only API then costs ~2 s per connection.
    assert _fast_loopback("http://localhost:8000") == "http://127.0.0.1:8000"
    assert _fast_loopback("http://localhost") == "http://127.0.0.1"
    assert _fast_loopback("http://api.example.com:8000") == "http://api.example.com:8000"
    assert ZenithAPI("http://localhost:8000/").base_url == "http://127.0.0.1:8000"


def test_clients_share_one_pooled_session_per_backend():
    a, b = ZenithAPI("http://localhost:8000"), ZenithAPI("http://127.0.0.1:8000")
    assert a.session is b.session
    assert ZenithAPI("http://127.0.0.1:9999").session is not a.session
