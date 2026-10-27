import argparse
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace


spec = importlib.util.spec_from_file_location("node_agent", Path(__file__).parents[1] / "dashboard/agent/eaf-node-agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def test_agent_refreshes_address_and_authenticates_heartbeat(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("a-private-test-token\n")
    config = argparse.Namespace(node_id="acer", name="Acer", address=None, role="server",
                                dashboard_url="https://example.com", interval=15,
                                probe_service=[], token_file=str(token))
    addresses = iter(["192.168.0.197", "192.168.178.200"])
    monkeypatch.setattr(agent, "detect_lan_ip", lambda: next(addresses))
    monkeypatch.setattr(agent, "collect_vitals", lambda: {})
    requests = []
    class Response(io.BytesIO):
        status = 200
    def send(request, **kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr(agent.urllib.request, "urlopen", send)
    assert agent.NodeAgent(config).send_heartbeat()
    assert requests[0].get_header("Authorization") == "Bearer a-private-test-token"
    assert json.loads(requests[0].data)["address"] == "192.168.178.200"


def test_agent_discovers_router_without_old_subnet(monkeypatch):
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps([
        dict(dev="wg-mgmt", gateway="10.60.0.1", metric=1),
        dict(dev="eth0", gateway="192.168.178.1", metric=100)])))
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect(self, target): assert target == ("192.168.178.1", 80)
        def getsockname(self): return ("192.168.178.200", 0)
    monkeypatch.setattr(agent.socket, "socket", lambda *a: Connection())
    assert agent.detect_lan_ip() == "192.168.178.200"
