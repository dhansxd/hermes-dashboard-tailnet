"""Run: python test_plugin.py with Hermes environment installed."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

from gateway.session_context import clear_session_vars, set_session_vars

spec = importlib.util.spec_from_file_location("dashboard_tailnet_test", Path(__file__).with_name("__init__.py"))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class Context:
    def __init__(self): self.names = []
    def register_command(self, name, handler, **kw):
        assert handler is p.handle
        self.names.append((name, kw))

ctx = Context()
p.register(ctx)
assert [name for name, _ in ctx.names] == ["dashboard", "dash", "db"]
assert all("args_hint" not in kw for _, kw in ctx.names)  # no-arg usage is safe for Telegram menu
assert p._route({}) == (None, False)
assert p._route({"Web": {"node.ts.net:8443": {"Handlers": {"/": {"Proxy": p.TARGET}}}}}) == (p.TARGET, False)
assert p._route({"Web": {"node.ts.net:8443": {"Handlers": {"/api": {"Proxy": p.TARGET}}}}}) == ("occupied-by-other-handler", False)
assert p._route({"AllowFunnel": {"node.ts.net:8443": True}}) == (None, True)

with patch.object(p, "_admins", return_value=set()), patch.object(p, "_on", side_effect=AssertionError("must not run")):
    token = set_session_vars(platform="telegram", chat_type="dm", user_id="111111")
    try: assert p.handle("on").startswith("Denied:")
    finally: clear_session_vars(token)

with patch.object(p, "_admins", return_value={"111111"}), patch.object(p, "_on", return_value="on-ok"):
    for platform, chat, user in [("discord", "dm", "111111"), ("telegram", "group", "111111"), ("telegram", "dm", "222222")]:
        token = set_session_vars(platform=platform, chat_type=chat, user_id=user)
        try: assert p.handle("on").startswith("Denied:")
        finally: clear_session_vars(token)
    token = set_session_vars(platform="telegram", chat_type="dm", user_id="111111")
    try:
        assert "Usage:" in p.handle("")
        assert p.handle("on") == "on-ok"
        assert p._LOCK.acquire(blocking=False)
        try: assert "already running" in p.handle("on")
        finally: p._LOCK.release()
    finally: clear_session_vars(token)

print("plugin tests OK")
