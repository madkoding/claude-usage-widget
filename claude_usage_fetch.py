"""Lee tu uso REAL del plan desde claude.ai/settings/usage con una ventana WebView2 propia.

Sesion propia (perfil en %APPDATA%/claude-usage-widget-motita/webview-profile): inicias sesion UNA vez con
`--login` (el widget lo ofrece solo y en su menu) y luego el widget lo ejecuta oculto cada pocos minutos.
No guarda tu contrasena ni lee cookies de tu navegador: solo extrae los porcentajes visibles de la
pagina y los escribe en rate_limits.json. No gasta cuota de Claude (solo carga una pagina).
Requiere: pip install pywebview (usa Edge WebView2, incluido en Windows 10/11).

Codigos de salida: 0 ok, 1 sin datos legibles, 2 hace falta iniciar sesion, 3 falta pywebview.
Errores detallados en %APPDATA%/claude-usage-widget-motita/fetch.log
"""
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "claude-usage-widget-motita"
OUT, STATUS, LOG = APP_DIR / "rate_limits.json", APP_DIR / "fetch_status.json", APP_DIR / "fetch.log"
PROFILE = APP_DIR / "webview-profile"
URL = "https://claude.ai/settings/usage"

# Texto del dialogo de ajustes (o de toda la pagina) + ruta actual
JS = ("JSON.stringify({p: location.pathname + location.hash, "
      "t: (document.querySelector('[role=dialog]') || document.body).innerText.slice(0, 8000), "
      "b: document.body.innerText.slice(0, 8000)})")

# ---------- parseo (puro, probado con el texto real de la pagina en espanol) ----------
_DOW = {"lun": 0, "mon": 0, "mar": 1, "tue": 1, "mie": 2, "mié": 2, "wed": 2, "jue": 3, "thu": 3,
        "vie": 4, "fri": 4, "sab": 5, "sáb": 5, "sat": 5, "dom": 6, "sun": 6}
_DOW_RX = r"(lun|mon|mar|tue|mi[eé]|wed|jue|thu|vie|fri|s[aá]b|sat|dom|sun)"
_TIME_AMPM = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b\.?", re.I)
_TIME_24 = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%\s*(?:usado|used|utilizado)", re.I)
_RESET = re.compile(r"(?:se restablece|restablece|resets?)\s*(.*)$", re.I)
_TITLES = {
    "five_hour": re.compile(r"^(sesi[oó]n actual|current session)\b", re.I),
    "seven_day": re.compile(r"^(esta semana|this week|all models|todos los modelos)\s*$", re.I),
    "fable_week": re.compile(r"^fable(\s+(esta semana|this week|weekly|semanal))?\s*$", re.I),
}


def _clock(s):
    m = _TIME_AMPM.search(s)
    if m:
        h, mi = int(m.group(1)) % 12, int(m.group(2) or 0)
        return h + (12 if m.group(3).lower() == "p" else 0), mi
    m = _TIME_24.search(s)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_reset(s, now):
    """'el dom, 4:30 a.m.' / 'Thu 5:00 AM' / 'en 2 h 30 min' / 'in 3 hr 12 min' -> datetime local o None."""
    m = re.search(r"\b(?:en|in)\s+(.*)$", s, re.I)
    if m:
        rest = m.group(1)
        d = re.search(r"(\d+)\s*(?:d\b|d[ií]as?|days?)", rest, re.I)
        h = re.search(r"(\d+)\s*(?:hrs?\b|h\b|horas?|hours?)", rest, re.I)
        mi = re.search(r"(\d+)\s*(?:min\w*|m\b)", rest, re.I)
        if d or h or mi:
            return now + timedelta(days=int(d.group(1)) if d else 0, hours=int(h.group(1)) if h else 0,
                                   minutes=int(mi.group(1)) if mi else 0)
    clk = _clock(s)
    if not clk:
        return None
    base = now.replace(hour=clk[0], minute=clk[1], second=0, microsecond=0)
    dm = re.search(r"\b" + _DOW_RX, s, re.I)
    if dm:
        base += timedelta(days=(_DOW[dm.group(1).lower()] - base.weekday()) % 7)
        return base if base > now else base + timedelta(days=7)
    if re.search(r"ma[nñ]ana|tomorrow", s, re.I):
        return base + timedelta(days=1)
    return base if base > now else base + timedelta(days=1)


def parse_usage(text, now=None):
    """Devuelve el dict listo para rate_limits.json o None si el texto no es la pagina de uso."""
    now = now or datetime.now().astimezone()
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    out = {}
    for i, line in enumerate(lines):
        for key, rx in _TITLES.items():
            if key in out or not rx.match(line):
                continue
            pct = reset = None
            for nxt in lines[i + 1:i + 6]:
                if any(t.match(nxt) for t in _TITLES.values()):
                    break
                if pct is None:
                    mp = _PCT.search(nxt)
                    if mp:
                        pct = float(mp.group(1).replace(",", "."))
                if reset is None:
                    mr = _RESET.search(nxt)
                    if mr:
                        reset = parse_reset(mr.group(1), now)
            if pct is not None:
                out[key] = {"used_percentage": pct, "resets_at": int(reset.timestamp()) if reset else None}
    if not out:
        return None
    prod = {}
    for i, line in enumerate(lines):
        if re.search(r"por producto|by product", line, re.I):
            k = i + 1
            while k + 1 < len(lines) and len(prod) < 8:
                if re.search(r"[úu]ltima actualizaci|last updated|m[aá]s informaci|learn more", lines[k], re.I):
                    break
                mp = re.match(r"^(\d{1,3})\s*%$", lines[k + 1])
                if mp:
                    name = lines[k]
                    prod["Code" if name.lower() == "claude code" else name] = int(mp.group(1))
                    k += 2
                else:
                    k += 1
            break
    plan = next((x for x in lines if re.match(r"^(Max|Pro|Team|Enterprise)\b.{0,10}$", x)), None)
    return {"plan": plan, "updated": time.time(), **out, "by_product": prod}


def extract(raw, now=None):
    """raw = JSON que devuelve JS. -> ('login', None) | ('ok', datos) | ('wait', None)."""
    try:
        o = json.loads(raw)
    except Exception:
        return "wait", None
    path, text, body = o.get("p", ""), o.get("t", ""), o.get("b", "")
    if "/login" in path or "/logout" in path or re.search(r"continuar con google|continue with google", body, re.I):
        return "login", None
    for t in (text, body):
        data = parse_usage(t, now)
        if data:
            return "ok", data
    return "wait", None


# ---------- E/S ----------
def _write(path, obj):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


RETRY_AFTER = [None]  # segundos pedidos por la API en un 429 (header Retry-After)


def status(ok, reason):
    try:
        _write(STATUS, {"ts": time.time(), "ok": ok, "reason": reason, "retry_after": None if ok else RETRY_AFTER[0]})
    except Exception:
        pass


def log(msg):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def _creds():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    f = Path(base) / ".credentials.json"
    return json.loads(f.read_text(encoding="utf-8")).get("claudeAiOauth") or {}


def _iso(s):
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def parse_oauth(js, creds=None, current=None):
    """Respuesta de /api/oauth/usage -> dict para rate_limits.json (o None si no trae ventanas)."""
    out = {}
    for key in ("five_hour", "seven_day"):
        w = js.get(key)
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            out[key] = {"used_percentage": float(w["utilization"]), "resets_at": _iso(w.get("resets_at"))}
    for k, w in js.items():  # ventana semanal de Fable si la API la expone (nombre exacto sin verificar)
        if "fable" in k.lower() and isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            out["fable_week"] = {"used_percentage": float(w["utilization"]), "resets_at": _iso(w.get("resets_at"))}
            break
    if not out:
        return None
    cur = dict(current or {})
    sub = str((creds or {}).get("subscriptionType") or "").lower()
    tier = str((creds or {}).get("rateLimitTier") or "").lower()
    plan = cur.get("plan")
    if sub == "max":
        plan = "Max (20x)" if "20x" in tier else "Max (5x)" if "5x" in tier else "Max"
    elif sub in ("pro", "team", "enterprise"):
        plan = sub.capitalize()
    cur.update(out)
    cur.update(plan=plan, updated=time.time())
    return cur


def fetch_oauth():
    """Token de Claude Code (~/.claude/.credentials.json) -> % real del plan, sin ventanas ni cookies.
    -> (codigo_de_salida, motivo). Solo lee el token: nunca lo refresca ni lo copia."""
    import urllib.error
    import urllib.request
    try:
        c = _creds()
        tok = c.get("accessToken")
    except FileNotFoundError:
        return 1, "no_credentials"
    except Exception as ex:
        log(f"credenciales ilegibles: {ex}")
        return 1, "no_credentials"
    if not tok:
        return 1, "no_credentials"
    exp = c.get("expiresAt")
    if isinstance(exp, (int, float)) and exp / 1000 < time.time():
        return 1, "token_expired"  # Claude Code lo renueva al usarlo: basta abrir `claude` una vez
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/json", "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-usage-widget-motita"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            js = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log(f"oauth usage HTTP {e.code}: {e.read()[:300]!r}")
        try:
            RETRY_AFTER[0] = max(0, int(e.headers.get("Retry-After")))
        except Exception:
            RETRY_AFTER[0] = None
        return 1, "oauth_429" if e.code == 429 else f"oauth_http_{e.code}"
    except Exception as ex:
        log(f"oauth usage error: {ex}")
        return 1, "oauth_error"
    try:
        cur = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    data = parse_oauth(js, c, cur if isinstance(cur, dict) else {})
    if not data:
        log(f"oauth usage sin ventanas conocidas: {str(js)[:300]}")
        return 1, "oauth_shape"
    _write(OUT, data)
    return 0, "ok"


def main(argv):
    login = "--login" in argv
    oauth_reason = None
    if not login:  # camino simple: token de Claude Code, sin pywebview ni sesion web
        code, oauth_reason = fetch_oauth()
        if code == 0:
            status(True, "ok")
            return 0
        if oauth_reason != "no_credentials":  # hay token pero fallo puntual: no abrir ventanas, reintentar luego
            status(False, oauth_reason)
            return 1
    try:
        import webview
    except Exception as ex:
        log(f"pywebview no disponible: {ex}")
        status(False, "pywebview_missing")
        return 3

    result = {"code": 1, "reason": "login_closed" if login else "timeout"}

    def worker(window):
        deadline = time.time() + (600 if login else 45)
        last_nav = time.time()
        try:
            while time.time() < deadline:
                time.sleep(2)
                try:
                    raw = window.evaluate_js(JS)
                except Exception:
                    continue
                kind, data = extract(raw) if raw else ("wait", None)
                if kind == "ok":
                    _write(OUT, data)
                    result.update(code=0, reason="ok")
                    break
                if kind == "login":
                    if not login:
                        result.update(code=2, reason="login_required")
                        break
                    continue  # esperando a que inicies sesion en la ventana
                if login and time.time() - last_nav > 8:  # ya con sesion pero fuera de la pagina de uso
                    last_nav = time.time()
                    window.load_url(URL)
        except Exception:
            log(traceback.format_exc())
            result.update(code=1, reason="error")
        finally:
            try:
                window.destroy()
            except Exception:
                pass

    try:
        window = webview.create_window("Claude usage · inicia sesión (con correo si Google falla)" if login else "Claude usage", URL,
                                       width=520, height=760, hidden=not login)
        PROFILE.mkdir(parents=True, exist_ok=True)
        webview.start(worker, window, private_mode=False, storage_path=str(PROFILE))
    except Exception:
        log(traceback.format_exc())
        result.update(code=1, reason="error")
    status(result["code"] == 0, result["reason"])
    return result["code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
