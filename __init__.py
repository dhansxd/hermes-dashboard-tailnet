"""Admin-only Telegram /dashboard on|off|status; no Hermes core changes."""

import json
import os
from pathlib import Path
import socket
import subprocess
import time
import shutil
import sys
import threading

from gateway.session_context import get_session_env
from hermes_constants import get_process_hermes_home


def _executable(name):
    found = shutil.which(name)
    if found:
        return found
    if name == "hermes":
        neighbor = Path(sys.executable).with_name("hermes")
        if neighbor.is_file():
            return str(neighbor)
    return None

HOME = get_process_hermes_home()
STATE = HOME / "dashboard-tailnet.json"
LOG = HOME / "logs" / "dashboard-tailnet.log"
LOCAL_PORT = 9119
HTTPS_PORT = 8443  # ponytail: one dedicated HTTPS endpoint; add port selection if 8443 is needed elsewhere.
TARGET = f"http://127.0.0.1:{LOCAL_PORT}"
_LOCK = threading.Lock()  # serialize concurrent Telegram commands within one gateway


def _admins():
    """Require explicit IDs; gateway allow_from alone is not an admin grant."""
    from hermes_cli.config import load_config_readonly
    entries = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
    raw = ((entries.get("dashboard-tailnet") or {}).get("settings") or {}).get("admin_ids")
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return {item.strip() for item in str(raw or "").split(",") if item.strip()}



def _child_env():
    env = os.environ.copy()
    env["HERMES_HOME"] = str(HOME)
    env["PATH"] = str(Path.home() / ".local/bin") + ":/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    return env


def _run(*args, timeout=15):
    executable = _executable(args[0])
    if not executable:
        raise RuntimeError(f"Program {args[0]} tidak ditemukan di PATH gateway.")
    return subprocess.run((executable, *args[1:]), capture_output=True, text=True, timeout=timeout, env=_child_env())


def _tailscale():
    result = _run("tailscale", "status", "--json")
    if result.returncode:
        raise RuntimeError("Tailscale belum tersedia: " + result.stderr.strip()[-250:])
    info = json.loads(result.stdout)
    if info.get("BackendState") != "Running":
        raise RuntimeError("Tailscale belum terhubung.")
    host = (info.get("Self") or {}).get("DNSName", "").rstrip(".")
    if not host or not host.endswith(".ts.net"):
        raise RuntimeError("DNS tailnet HTTPS belum tersedia.")
    return f"https://{host}:{HTTPS_PORT}"


def _serve():
    result = _run("tailscale", "serve", "status", "--json")
    if result.returncode:
        raise RuntimeError("Status Tailscale Serve gagal: " + result.stderr.strip()[-250:])
    return json.loads(result.stdout)


def _route(config):
    """Return proxy and whether this HTTPS port is accidentally public via Funnel."""
    port = f":{HTTPS_PORT}"
    web = next((v for k, v in config.get("Web", {}).items() if k.endswith(port)), {})
    handlers = web.get("Handlers", {})
    proxy = (handlers.get("/", {}) or {}).get("Proxy")
    if handlers and (set(handlers) != {"/"} or not proxy):
        proxy = "occupied-by-other-handler"
    funnel = any(k.endswith(port) and v for k, v in config.get("AllowFunnel", {}).items())
    return proxy, funnel


def _listener_pid():
    result = _run("lsof", "-t", "-nP", f"-iTCP:{LOCAL_PORT}", "-sTCP:LISTEN")
    pids = {int(pid) for pid in result.stdout.splitlines() if pid.isdigit()}
    return pids.pop() if result.returncode == 0 and len(pids) == 1 else None


def _listening():
    try:
        with socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=0.4):
            return True
    except OSError:
        return False


def _read_state():
    try:
        return json.loads(STATE.read_text())
    except FileNotFoundError:
        return {}


def _save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, STATE)


def _owned_alive(state):
    pid = state.get("pid")
    if not isinstance(pid, int) or pid < 2:
        return False
    proc = _run("ps", "-p", str(pid), "-o", "lstart=")
    command = _run("ps", "-p", str(pid), "-o", "command=")
    # Never signal another process after PID reuse or a foreign process.
    return (proc.returncode == command.returncode == 0
            and proc.stdout.strip() == state.get("started")
            and "dashboard" in command.stdout
            and "--port" in command.stdout and "9119" in command.stdout
            and "--host" in command.stdout and "127.0.0.1" in command.stdout)


def _stop_owned(state):
    if not _owned_alive(state):
        return
    pid = state["pid"]
    os.kill(pid, 15)
    for _ in range(30):
        if not _owned_alive(state):
            return
        time.sleep(0.1)
    raise RuntimeError("Dashboard belum berhenti; periksa proses sebelum mencoba lagi.")


def _status():
    state = _read_state()
    proxy, funnel = _route(_serve())
    try:
        url = _tailscale()
    except RuntimeError:
        url = "Tailscale offline"
    running = _listening()
    return (f"Dashboard: {'aktif' if running else 'mati'}; "
            f"Serve :{HTTPS_PORT}: {'aktif' if proxy == TARGET and not funnel else 'tidak aktif' if not proxy else 'konflik'}; "
            f"proses plugin: {'aktif' if _owned_alive(state) else 'tidak aktif'}. "
            + (f"URL: {url}" if running and proxy == TARGET and not funnel else ""))


def _on():
    url = _tailscale()
    state = _read_state()
    proxy, funnel = _route(_serve())
    if funnel or (proxy and proxy != TARGET):
        return f"Ditolak: endpoint HTTPS :{HTTPS_PORT} sudah dipakai. Tidak mengubah Serve."
    if proxy == TARGET and not state.get("serve_owned"):
        return "Ditolak: endpoint Tailscale sudah dikelola pihak lain."
    if state.get("serve_owned") and proxy != TARGET:
        return "Ditolak: Serve berubah di luar plugin. Periksa manual sebelum menyalakan lagi."
    if _listening() and not _owned_alive(state):
        return f"Ditolak: port {LOCAL_PORT} sudah dipakai proses lain."
    if _owned_alive(state) and proxy == TARGET:
        return f"Dashboard active: {url}\nTailnet only; Hermes login required."
    if _owned_alive(state):
        return "Ditolak: dashboard hidup tetapi Serve mati. Jalankan /dashboard off dulu."
    # public_url makes Hermes enforce login even though Tailscale proxies into loopback.
    from hermes_cli.config import load_config
    auth = (load_config().get("dashboard") or {}).get("basic_auth") or {}
    if not auth.get("username") or not (auth.get("password_hash") or auth.get("password")):
        return "Ditolak: autentikasi dashboard belum dikonfigurasi."
    config = load_config().get("dashboard") or {}
    if config.get("public_url") != url:
        result = _run("hermes", "config", "set", "dashboard.public_url", url)
        if result.returncode:
            return "Gagal menyetel dashboard.public_url: " + result.stderr.strip()[-250:]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    executable = _executable("hermes")
    if not executable:
        return "Hermes executable not found on the gateway PATH."
    command = [executable, "dashboard", "--no-open", "--host", "127.0.0.1",
               "--port", str(LOCAL_PORT)]
    with LOG.open("ab") as log:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True, env=_child_env())
    state = {"pid": None, "started": "", "serve_owned": proxy == TARGET and bool(state.get("serve_owned"))}
    _save_state(state)
    for _ in range(240):  # allow first-run frontend build up to ~60 seconds
        if _listening():
            break
        if proc.poll() is not None:
            STATE.unlink(missing_ok=True)
            return f"Dashboard gagal menyala. Periksa {LOG}."
        time.sleep(0.25)
    else:
        proc.terminate()
        proc.wait(timeout=5)
        STATE.unlink(missing_ok=True)
        return f"Dashboard tidak merespons di {LOCAL_PORT}. Periksa {LOG}."
    pid = _listener_pid()
    if pid != proc.pid:
        proc.terminate()
        proc.wait(timeout=5)
        STATE.unlink(missing_ok=True)
        return "Listener dashboard bukan proses yang plugin jalankan; Serve tidak diaktifkan."
    state["pid"] = pid
    state["started"] = _run("ps", "-p", str(pid), "-o", "lstart=").stdout.strip()
    _save_state(state)
    if not _owned_alive(state):
        proc.terminate()
        proc.wait(timeout=5)
        STATE.unlink(missing_ok=True)
        return "Identitas proses dashboard tidak cocok; tidak mengaktifkan Serve."
    if proxy != TARGET:
        result = _run("tailscale", "serve", "--bg", "--yes", f"--https={HTTPS_PORT}", TARGET, timeout=30)
        if result.returncode:
            _stop_owned(state)
            STATE.unlink(missing_ok=True)
            return "Serve gagal; dashboard dimatikan: " + result.stderr.strip()[-350:]
        state["serve_owned"] = True
        _save_state(state)
    proxy, funnel = _route(_serve())
    if proxy != TARGET or funnel:
        return "Serve belum terverifikasi aman; periksa `tailscale serve status` dan matikan manual."
    return f"Dashboard aktif: {url}\nAkses hanya dari tailnet; login Hermes tetap wajib."


def _off():
    state = _read_state()
    proxy, funnel = _route(_serve())
    if funnel or (proxy and proxy != TARGET):
        return "Ditolak: endpoint Serve berubah atau Funnel aktif; tidak mengubah layanan lain."
    if proxy == TARGET and not state.get("serve_owned"):
        return "Ditolak: endpoint Serve tidak tercatat milik plugin."
    if proxy == TARGET:
        result = _run("tailscale", "serve", f"--https={HTTPS_PORT}", "off")
        if result.returncode:
            return "Gagal mematikan Serve: " + result.stderr.strip()[-350:]
        if _route(_serve())[0]:
            return "Serve masih aktif; dashboard dibiarkan menyala."
    if _owned_alive(state):
        _stop_owned(state)
    STATE.unlink(missing_ok=True)
    return "Serve dashboard mati; proses dashboard milik plugin dihentikan. Gateway dan Tailscale tetap berjalan."


def handle(raw):
    if (get_session_env("HERMES_SESSION_PLATFORM") != "telegram"
            or str(get_session_env("HERMES_SESSION_USER_ID") or "") not in _admins()
            or get_session_env("HERMES_SESSION_CHAT_TYPE") not in ("dm", "private", "direct")):
        return "Denied: configure admin_ids; commands require an authorized Telegram DM."
    action = raw.strip().lower()
    if action not in ("on", "off", "status"):
        return "Usage: /db on | /db off | /db status (also /dash, /dashboard)"
    if not _LOCK.acquire(blocking=False):
        return "Dashboard command already running; try again in a moment."
    try:
        return {"on": _on, "off": _off, "status": _status}[action]()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return f"Dashboard {action} failed: {exc}"
    finally:
        _LOCK.release()


def register(ctx):
    for name in ("dashboard", "dash", "db"):
        ctx.register_command(name, handle,
                             description="Dashboard + Tailscale Serve: on, off, status (Telegram DM admin)")
