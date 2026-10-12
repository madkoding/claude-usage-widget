"""Claude usage widget (estilo Motita): 3 gauges circulares con antialias.

Fuentes de datos:
  0) Plan real AUTOMATICO: claude_usage_fetch.py (misma carpeta) lee claude.ai/settings/usage con tu
     sesion en una ventana WebView2 propia (pip install pywebview; login unico desde el menu) cada
     fetch_every_s segundos. No gasta cuota. Escribe %APPDATA%/claude-usage-widget-motita/rate_limits.json.
  1) Plan real (recomendado): % de las ventanas de 5h y 7d que Claude Code expone en su
     statusline. Lo guarda claude_usage_statusline.py en
     %APPDATA%/claude-usage-widget-motita/rate_limits.json (solo suscriptores Pro/Max).
  2) Estimado local: tokens leidos de los logs de Claude Code
     (~/.claude/projects/**/*.jsonl), como % de tu pico de 30 dias.
  Menu hamburguesa (o clic derecho): siempre encima, bloquear posicion, opacidad, tamano, modo
  compacto, gauge de Fable (opcional), anclar a esquina, alertas, iniciar con Windows.
  Se redimensiona arrastrando la esquina inferior derecha o con Ctrl+rueda (conserva la proporcion).
  Atajo global (Windows): Ctrl+Alt+U oculta/muestra el widget.
  Sin Claude Code local, rate_limits.json (junto al script o en %APPDATA%) se genera leyendo
  claude.ai/settings/usage; las ventanas caducan solas al llegar su hora de reinicio.

Config opcional en %APPDATA%/claude-usage-widget-motita/config.json:
  {"block_limit": 0, "week_limit": 0,
   "refresh_s": 30, "fetch_every_s": 300, "opacity": 0.96, "scale": 1.0,
   "topmost": true, "locked": false, "compact": false, "show_fable": false,
   "alerts": true, "alert_pct": 80,
   "extra_roots": ["D:/ruta/a/projects"], "wsl_distro": "Ubuntu"}
  ("wsl_distro" activa leer los logs dentro de WSL; puede arrancar esa distro.)

Fuentes Motita (Sansation / JetBrains Mono NF): carpeta claude_usage_widget_fonts
junto al script; si falta usa Segoe UI / Consolas.

Uso:  pythonw claude_usage_widget.py   (sin consola)
"""
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BLOCK_HOURS = 5
HISTORY_DAYS = 31
HERE = Path(__file__).resolve().parent
FONT_DIR = HERE / "claude_usage_widget_fonts"
APP_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "claude-usage-widget-motita"
CFG_PATH = APP_DIR / "config.json"
RATE_PATH = APP_DIR / "rate_limits.json"
STATUS_PATH = APP_DIR / "fetch_status.json"
FETCH = HERE / "claude_usage_fetch.py"
DEFAULTS = {"x": None, "y": None, "refresh_s": 30, "block_limit": 0, "week_limit": 0,
            "opacity": 0.96, "scale": 1.0, "topmost": True, "locked": False, "compact": False,
            "show_fable": False, "alerts": True, "alert_pct": 80,
            "extra_roots": [], "wsl_distro": "", "fetch_every_s": 300}
SCALE_MIN, SCALE_MAX = 0.6, 2.5

# Tokens Motita (tokens.css, dark only)
BASE, SURF = "#0a0a0f", "#16161e"
BORDER, TRACK = "#2a2a32", "#26262e"
FG, MUTED = "#e8e8ea", "#9a9aaa"
TONES = {"accent": "#4cc2ff", "warning": "#f0a040", "danger": "#ff6b6b", "muted": "#9a9aaa"}


# ---------- datos ----------
def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_cfg(cfg):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CFG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def project_roots(cfg):
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    bases = [Path(p.strip()) for p in env.split(",") if p.strip()] if env else []
    bases += [Path.home() / ".config" / "claude", Path.home() / ".claude"]
    roots = [b / "projects" for b in bases]
    roots += [Path(p) for p in (cfg.get("extra_roots") or [])]
    distro = cfg.get("wsl_distro")
    if distro and sys.platform == "win32":
        for base in (r"\\wsl.localhost", r"\\wsl$"):
            try:
                found = [p for pat in ("home/*/.claude/projects", "home/*/.config/claude/projects",
                                       "root/.claude/projects") for p in Path(base, distro).glob(pat)]
            except OSError:
                found = []
            if found:
                roots += found
                break
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


_file_cache = {}  # path -> (mtime, size, entries)
_lock = threading.Lock()


def _parse_file(path):
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                o = json.loads(line)
                msg = o.get("message") or {}
                u = msg.get("usage")
                ts = o.get("timestamp")
                if not u or not ts:
                    continue
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                key = f"{msg.get('id')}:{o.get('requestId')}" if msg.get("id") and o.get("requestId") else None
                out.append((t, key,
                            int(u.get("input_tokens") or 0),
                            int(u.get("output_tokens") or 0),
                            int(u.get("cache_creation_input_tokens") or 0),
                            int(u.get("cache_read_input_tokens") or 0)))
            except Exception:
                continue
    return out


def load_entries(roots, days=HISTORY_DAYS):
    cutoff = time.time() - (days + 1) * 86400
    cut_dt = datetime.now(timezone.utc) - timedelta(days=days)
    seen, entries = set(), []
    for root in roots:
        for p in root.rglob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            c = _file_cache.get(p)
            if not c or c[0] != st.st_mtime or c[1] != st.st_size:
                c = (st.st_mtime, st.st_size, _parse_file(p))
                _file_cache[p] = c
            for e in c[2]:
                if e[0] < cut_dt or (e[1] and e[1] in seen):
                    continue
                if e[1]:
                    seen.add(e[1])
                entries.append(e)
    entries.sort(key=lambda e: e[0])
    return entries


def total(es):
    """tokens 'de trabajo' = input + output + cache write; cache read aparte."""
    return sum(e[2] + e[3] + e[4] for e in es), sum(e[5] for e in es)


def build_blocks(entries):
    """Bloques de 5h estilo ccusage: empiezan en la hora cerrada del primer mensaje."""
    blocks, start, end, last, cur = [], None, None, None, None
    for e in entries:
        t = e[0]
        if start is None or t >= end or (t - last) >= timedelta(hours=BLOCK_HOURS):
            start = t.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=BLOCK_HOURS)
            cur = []
            blocks.append((start, end, cur))
        cur.append(e)
        last = t
    return blocks


def _read_json(p):
    try:
        o = json.loads(Path(p).read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def _num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def load_rate(path=None):
    """% real del plan (rate_limits.json en %APPDATA% o junto al script; gana el mas reciente).
    Ventanas: five_hour, seven_day, fable_week. Descarta las ya reiniciadas.
    Devuelve (ventanas, meta) con meta = {"plan": ..., "by_product": {...}}."""
    cands = [path] if path else [RATE_PATH, HERE / "rate_limits.json"]
    objs = [o for o in (_read_json(p) for p in cands) if o]
    if not objs:
        return {}, {}
    o = max(objs, key=lambda x: _num(x.get("updated")))
    age = max(0.0, time.time() - _num(o.get("updated")))
    out = {}
    for k in ("five_hour", "seven_day", "fable_week"):
        w = o.get(k) or {}
        try:
            pct = float(w["used_percentage"])
        except Exception:
            continue
        try:
            ra = float(w["resets_at"]) if w.get("resets_at") is not None else None
        except Exception:
            ra = None
        # sin hora de reinicio conocida, el dato vale 25 min desde la lectura
        if (ra is not None and ra > time.time()) or (ra is None and age < 1500):
            out[k] = {"pct": pct, "age": age,
                      "resets_at": datetime.fromtimestamp(ra, timezone.utc) if ra is not None else None}
    meta = {"plan": o.get("plan"), "by_product": o.get("by_product") or {}} if out else {}
    return out, meta


def read_rate(path=None):
    return load_rate(path)[0]


def fetch_delay(every, fails=0, retry_after=0):
    """Segundos hasta el proximo fetch: base (min 120 s) x2 por fallo seguido (hasta x16), o Retry-After (tope 1 h) si es mayor. 0 = desactivado."""
    if every <= 0:
        return 0
    return max(max(120, every) * 2 ** min(fails, 4), min(retry_after or 0, 3600))


def read_status():
    """Resultado del ultimo intento del lector: {ok, reason, ts}."""
    return _read_json(STATUS_PATH) or {}


def spawn_fetch(login=False):
    """Lanza claude_usage_fetch.py sin ventana de consola (con --login muestra la ventana de inicio de sesion)."""
    if not getattr(sys, "frozen", False) and not FETCH.exists():
        return None
    base = [sys.executable, "--fetch"] if getattr(sys, "frozen", False) else [sys.executable, str(FETCH)]
    args = base + (["--login"] if login else [])
    try:
        return subprocess.Popen(args, creationflags=0x08000000 if sys.platform == "win32" else 0,  # NO_WINDOW
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None


def compute(cfg=None, now=None):
    cfg = cfg or {}
    now = now or datetime.now(timezone.utc)
    roots = project_roots(cfg)
    with _lock:
        es = load_entries(roots)
    blocks = build_blocks(es)
    active = blocks[-1] if blocks and now < blocks[-1][1] else None
    block_work = total(active[2])[0] if active else 0
    peak_block = max((total(b[2])[0] for b in blocks), default=0)

    daily = {}
    for e in es:
        d = e[0].astimezone().date()
        daily[d] = daily.get(d, 0) + e[2] + e[3] + e[4]
    today = datetime.now().astimezone().date()

    def wsum(d):
        return sum(daily.get(d - timedelta(days=i), 0) for i in range(7))

    week_work = wsum(today)
    rate, meta = load_rate()
    return {
        "block_work": block_work,
        "block_remaining": (active[1] - now) if active else None,
        "peak_block": max(peak_block, block_work),
        "week_work": week_work,
        "cache_week": sum(e[5] for e in es if e[0] >= now - timedelta(days=7)),
        "peak_week": max([wsum(d) for d in daily] + [week_work]),
        "rate": rate,
        "rate_meta": meta,
        "sources": len(roots),
        "entries": len(es),
    }


# ---------- formato ----------
def fmt_tok(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def fmt_delta(td):
    s = max(int(td.total_seconds()), 0)
    h, m = s // 3600, (s % 3600) // 60
    if h >= 24:
        return f"{h // 24}d {h % 24}h"
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_age(sec):
    if sec < 90:
        return "ahora"
    if sec < 5400:
        return f"hace {int(sec // 60)}m"
    return f"hace {int(sec // 3600)}h"


# Escala de severidad: verde -> verde-amarillo -> amarillo -> naranja-rojo -> rojo (0, 25, 50, 75, 100 %)
_SCALE = [(0, (0x3d, 0xdc, 0x84)), (25, (0xa6, 0xd9, 0x4a)), (50, (0xf2, 0xc9, 0x4c)),
          (75, (0xf0, 0x8a, 0x3c)), (100, (0xff, 0x52, 0x52))]


def gauge_color(pct):
    """Color del anillo interpolado entre los 5 tramos segun el porcentaje (tope en 0 y 100)."""
    p = max(0.0, min(100.0, float(pct)))
    for (p0, c0), (p1, c1) in zip(_SCALE, _SCALE[1:]):
        if p <= p1:
            t = (p - p0) / (p1 - p0)
            return "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))
    return "#%02x%02x%02x" % _SCALE[-1][1]


def tone_for(pct, has_data):
    """Motita: accent normal, warning cerca del limite, danger por encima. Siempre con una palabra."""
    if not has_data:
        return "muted", "SIN DATOS"
    if pct > 100:
        return "danger", "SOBRE LIMITE"
    if pct >= 80:
        return "warning", "ALTO"
    return "accent", "NORMAL"


# ---------- anillo con antialias ----------
def gauge_geometry(w, h, stroke_px):
    """Centro, radio medio del trazo y grosor (~8% del diametro, minimo stroke_px)."""
    d = min(w, h) - 4
    stroke = max(stroke_px, d * 0.08)
    return w / 2.0, h / 2.0, d / 2.0 - stroke / 2.0, stroke


def _rgb(hexc):
    return tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5))


def render_ring(w, h, frac, arc_hex, track_hex, bg_hex, stroke_px=8):
    """Pista + arco horario desde las 12h con extremos redondeados, en una cadena apta para
    PhotoImage.put. Antialias por distancia con signo: la cobertura de cada pixel sale de su
    distancia al trazo (arco = capsula curva), sin dependencias externas."""
    cx, cy, rm, stroke = gauge_geometry(w, h, stroke_px)
    half = stroke / 2.0
    bg, tr, ar = _rgb(bg_hex), _rgb(track_hex), _rgb(arc_hex)
    f = min(max(frac, 0.0), 1.0)
    theta = 2 * math.pi * f
    has_arc, full = f > 0.002, f >= 0.998
    ax, ay = cx, cy - rm
    bx, by = cx + rm * math.sin(theta), cy - rm * math.cos(theta)
    lo, hi = max(rm - half - 1.5, 0.0), rm + half + 1.5
    bg_row = ["#%02x%02x%02x" % bg] * w
    bg_str = " ".join(bg_row)
    rows = []
    for y in range(h):
        py = y + 0.5
        dy = py - cy
        if abs(dy) > hi:
            rows.append(bg_str)
            continue
        row = bg_row[:]
        xr = math.sqrt(hi * hi - dy * dy)
        if abs(dy) < lo:
            xi = math.sqrt(lo * lo - dy * dy)
            spans = [(cx - xr, cx - xi), (cx + xi, cx + xr)]
        else:
            spans = [(cx - xr, cx + xr)]
        for a, b in spans:
            for x in range(max(0, int(a)), min(w, int(b) + 1)):
                pxx = x + 0.5
                dx = pxx - cx
                r = math.hypot(dx, dy)
                band = abs(r - rm)
                ct = half + 0.5 - band
                if ct <= 0:
                    continue
                ct = min(ct, 1.0)
                c0 = bg[0] + (tr[0] - bg[0]) * ct
                c1 = bg[1] + (tr[1] - bg[1]) * ct
                c2 = bg[2] + (tr[2] - bg[2]) * ct
                if has_arc:
                    if full:
                        d = band
                    else:
                        phi = math.atan2(dx, -dy) % (2 * math.pi)
                        d = band if phi <= theta else min(math.hypot(pxx - ax, py - ay),
                                                          math.hypot(pxx - bx, py - by))
                    ca = half + 0.5 - d
                    if ca > 0:
                        ca = min(ca, 1.0)
                        c0 += (ar[0] - c0) * ca
                        c1 += (ar[1] - c1) * ca
                        c2 += (ar[2] - c2) * ca
                row[x] = "#%02x%02x%02x" % (int(c0 + 0.5), int(c1 + 0.5), int(c2 + 0.5))
        rows.append(" ".join(row))
    return "{" + "} {".join(rows) + "}", cx, cy


def alert_step(armed, pct, has, th):
    """Histeresis de la alerta. armed = ya aviso (o ya estaba sobre el umbral). Devuelve (armed, avisar)."""
    over = has and pct >= th
    if over and not armed:
        return True, True
    if not has or pct < th - 10:
        return False, False
    return armed, False


# ---------- inicio con Windows / atajo global ----------
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "ClaudeUsageWidget"


def autostart_cmd():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else exe}" "{Path(__file__).resolve()}"'


def autostart_get():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, RUN_NAME)[0])
    except OSError:
        return False


def autostart_set(on):
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_cmd())
            else:
                winreg.DeleteValue(k, RUN_NAME)
    except OSError:
        pass


def start_hotkey(q, mods=0x1 | 0x2 | 0x4000, vk=0x55):
    """Ctrl+Alt+U global (Windows): un hilo con su bucle de mensajes avisa por la cola."""
    if sys.platform != "win32":
        return

    def loop():
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        if not u.RegisterHotKey(None, 1, mods, vk):
            return
        m = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
            if m.message == 0x0312:  # WM_HOTKEY
                q.put("toggle")

    threading.Thread(target=loop, daemon=True).start()


def start_tray(q):
    """Icono en la bandeja (opcional: requiere pystray y Pillow). Devuelve True si arranco."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return False
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), outline=TONES["accent"], width=8)
    d.ellipse((24, 26, 30, 32), fill=TONES["accent"])
    d.ellipse((36, 26, 42, 32), fill=TONES["accent"])
    menu = pystray.Menu(pystray.MenuItem("Mostrar / ocultar", lambda: q.put("toggle"), default=True),
                        pystray.MenuItem("Salir", lambda: q.put("quit")))
    icon = pystray.Icon("claude-usage", img, "Claude usage", menu)
    icon.run_detached()
    return True


# ---------- UI ----------
def _enable_dpi():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def _load_private_fonts():
    if sys.platform != "win32" or not FONT_DIR.is_dir():
        return
    import ctypes
    for p in sorted(FONT_DIR.glob("*.ttf")):
        try:
            ctypes.windll.gdi32.AddFontResourceExW(str(p), 0x10, 0)  # FR_PRIVATE
        except Exception:
            pass


def run_ui():
    import tkinter as tk
    from tkinter import font as tkfont

    _enable_dpi()
    _load_private_fonts()
    cfg = load_cfg()

    root = tk.Tk()
    root.title("Claude usage")
    root.overrideredirect(True)
    root.attributes("-topmost", bool(cfg["topmost"]))
    try:
        root.attributes("-alpha", float(cfg["opacity"]))
    except Exception:
        pass
    root.configure(bg=BORDER)

    S = root.winfo_fpixels("1i") / 96.0
    sc = {"v": min(max(_num(cfg["scale"]) or 1.0, SCALE_MIN), SCALE_MAX)}

    def px(n):
        return int(round(n * S * sc["v"]))

    fams = set(tkfont.families(root))
    sans = "Sansation" if "Sansation" in fams else "Segoe UI"
    mono = next((f for f in sorted(fams) if f.lower().startswith("jetbrainsmono")), None) or "Consolas"

    specs = {"title": (mono, 12, True), "close": (sans, 18, False), "label": (mono, 11, True),
             "word": (mono, 10, True), "fig": (sans, 28, True), "unit": (sans, 14, False),
             "val": (sans, 16, True), "tok": (sans, 12, False), "cap": (sans, 12, False),
             "meta": (sans, 10, False)}
    fonts = {k: tkfont.Font(root=root, family=f, size=-10, weight="bold" if b else "normal")
             for k, (f, _, b) in specs.items()}
    f_fig, f_unit = fonts["fig"], fonts["unit"]

    def refont():
        for k, (_, size, _b) in specs.items():
            fonts[k].configure(size=-px(size))

    refont()

    inner = tk.Frame(root, bg=SURF)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    ui = {}  # widgets que se reconstruyen al cambiar de tamano

    def build():
        for ch in inner.winfo_children():
            ch.destroy()
        ui["W"] = px(200 if cfg["compact"] else 248)
        ui["GW"] = GW = ui["W"] - 2 - 2 * px(16)
        ui["GH"] = GH = px(136)

        # cabecera: menu hamburguesa + marca (aproximada) + titulo + cerrar
        head = tk.Frame(inner, bg=SURF)
        head.pack(fill="x", padx=px(16), pady=(px(14), px(4)))
        ham = tk.Label(head, text="\u2630", font=fonts["close"], fg=MUTED, bg=SURF, cursor="hand2")
        ham.pack(side="left", padx=(0, px(8)))
        ham.bind("<Button-1>", lambda e: menu.tk_popup(ham.winfo_rootx(), ham.winfo_rooty() + ham.winfo_height()))
        ham.bind("<Enter>", lambda e: ham.config(fg=FG))
        ham.bind("<Leave>", lambda e: ham.config(fg=MUTED))
        ms = px(22)
        mark = tk.Canvas(head, width=ms, height=ms, bg=SURF, highlightthickness=0, bd=0)
        mark.pack(side="left")
        k = ms / 32.0
        pts = [(7, 25), (5, 13), (7, 5), (13, 10), (19, 10), (25, 5), (27, 13), (25, 25), (21, 27), (11, 27),
               (7, 25)]
        mark.create_line(*[v * k for p in pts for v in p], fill=TONES["accent"], width=max(2, int(round(2 * k))),
                         joinstyle="round", capstyle="round")
        for ex in (12, 20):
            mark.create_oval((ex - 1.6) * k, (17 - 1.6) * k, (ex + 1.6) * k, (17 + 1.6) * k,
                             fill=TONES["accent"], outline=TONES["accent"])
        tk.Label(head, text="CLAUDE USAGE", font=fonts["title"], fg=FG, bg=SURF).pack(side="left", padx=(px(8), 0))
        close = tk.Label(head, text="\u00d7", font=fonts["close"], fg=MUTED, bg=SURF, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: quit_app())
        close.bind("<Enter>", lambda e: close.config(fg=FG))
        close.bind("<Leave>", lambda e: close.config(fg=MUTED))

        def hairline():
            tk.Frame(inner, height=1, bg=BORDER).pack(fill="x", padx=px(16), pady=(px(6), 0))

        def section(title):
            hairline()
            f = tk.Frame(inner, bg=SURF)
            f.pack(fill="x", padx=px(16), pady=(px(10), px(4)))
            row = tk.Frame(f, bg=SURF)
            row.pack(fill="x")
            ttl = tk.Label(row, text=title, font=fonts["label"], fg=MUTED, bg=SURF)
            ttl.pack(side="left")
            word = tk.Label(row, text="", font=fonts["word"], fg=MUTED, bg=SURF)
            word.pack(side="right")
            cv = tk.Canvas(f, width=GW, height=GH, bg=SURF, highlightthickness=0, bd=0)
            cv.pack(pady=(px(8), px(4)))
            img = tk.PhotoImage(master=root, width=GW, height=GH)
            vr = tk.Frame(f, bg=SURF)
            vr.pack()
            val = tk.Label(vr, text="0", font=fonts["val"], fg=FG, bg=SURF)
            val.pack(side="left")
            unit = tk.Label(vr, text="tokens", font=fonts["tok"], fg=MUTED, bg=SURF)
            unit.pack(side="left", padx=(px(4), 0), pady=(px(2), 0))
            d1 = tk.Label(f, text="", font=fonts["cap"], fg=MUTED, bg=SURF)
            d1.pack()
            d2 = tk.Label(f, text="", font=fonts["meta"], fg=MUTED, bg=SURF)
            d2.pack()
            return {"cv": cv, "img": img, "title": ttl, "word": word, "val": val, "unit": unit, "d1": d1, "d2": d2}

        def line(title):  # modo compacto: titulo y porcentaje en una sola linea
            hairline()
            row = tk.Frame(inner, bg=SURF)
            row.pack(fill="x", padx=px(16), pady=(px(8), px(2)))
            ttl = tk.Label(row, text=title, font=fonts["label"], fg=MUTED, bg=SURF)
            ttl.pack(side="left")
            pct = tk.Label(row, text="\u2013", font=fonts["val"], fg=FG, bg=SURF)
            pct.pack(side="right")
            return {"compact": True, "title": ttl, "pct": pct}

        make = line if cfg["compact"] else section
        ui["block"] = make("SESI\u00d3N 5H")
        ui["week"] = make("SEMANA \u00b7 7D")
        ui.pop("fable", None)
        if cfg["show_fable"]:
            ui["fable"] = make("FABLE \u00b7 7D")
        hairline()
        ui["footer"] = tk.Label(inner, text="cargando\u2026", font=fonts["meta"], fg=MUTED, bg=SURF)
        ui["foot2"] = tk.Label(inner, text="", font=fonts["meta"], fg=MUTED, bg=SURF, wraplength=GW,
                               justify="center")
        if cfg["compact"]:
            tk.Frame(inner, height=px(8), bg=SURF).pack()
        else:
            ui["footer"].pack(pady=(px(6), 0))
            ui["foot2"].pack(pady=(0, px(10)))
        bind_all(inner)

    alerted = {}

    def toast(msg):
        t = tk.Toplevel(root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=TONES["warning"])
        tk.Label(t, text=msg, font=fonts["cap"], fg=FG, bg=SURF, padx=px(12), pady=px(8)).pack(padx=1, pady=1)
        t.update_idletasks()
        tx = min(max(0, root.winfo_x()), root.winfo_screenwidth() - t.winfo_reqwidth())
        ty = root.winfo_y() - t.winfo_reqheight() - px(6)
        t.geometry(f"+{tx}+{ty if ty >= 0 else root.winfo_y() + root.winfo_height() + px(6)}")
        root.bell()
        t.after(6000, t.destroy)

    def check_alert(sec, pct, has):
        """Avisa una vez al cruzar el umbral; rearma al bajar 10 puntos. El primer dato no avisa."""
        th, key = float(cfg["alert_pct"]), id(sec)
        if key not in alerted:
            alerted[key] = has and pct >= th
            return
        alerted[key], fire = alert_step(alerted[key], pct, has, th)
        if fire and cfg["alerts"]:
            toast(f"{sec['title'].cget('text')}: {pct:.0f}% del l\u00edmite")

    def paint(sec, pct, has, val, unit, line1, line2, title=None):
        key, word = tone_for(pct, has)
        color = gauge_color(pct) if has else TONES[key]
        check_alert(sec, pct, has)
        if title:
            sec["title"].config(text=title)
        if sec.get("compact"):
            sec["pct"].config(text=f"{min(pct, 999):.0f}%" if has else "\u2013", fg=color)
            return
        data, cx, cy = render_ring(ui["GW"], ui["GH"], pct / 100.0, color, TRACK, SURF, px(8))
        sec["img"].put(data, to=(0, 0))
        c = sec["cv"]
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=sec["img"])
        txt = f"{min(pct, 999):.0f}" if has else "\u2013"
        fw, uw = f_fig.measure(txt), f_unit.measure("%")
        x0 = cx - (fw + (px(2) + uw if has else 0)) / 2
        base_y = cy + px(28) * 0.35  # linea base de los digitos, centrada en el anillo
        c.create_text(x0, base_y + f_fig.metrics("descent"), text=txt, font=f_fig, fill=FG, anchor="sw")
        if has:
            c.create_text(x0 + fw + px(2), base_y + f_unit.metrics("descent"), text="%", font=f_unit,
                          fill=MUTED, anchor="sw")
        sec["word"].config(text=word, fg=color)
        sec["val"].config(text=val)
        sec["unit"].config(text=unit)
        sec["d1"].config(text=line1)
        sec["d2"].config(text=line2)

    def local_pct(work, limit_cfg, peak):
        lim = limit_cfg if limit_cfg > 0 else peak
        text = (f"l\u00edmite {fmt_tok(lim)}" if limit_cfg > 0 else f"pico 30d {fmt_tok(lim)} \u00b7 local") \
            if lim > 0 else "sin historial"
        return (work / lim * 100.0 if lim > 0 else 0.0), lim > 0, text

    DAYS = ["lun", "mar", "mi\u00e9", "jue", "vie", "s\u00e1b", "dom"]

    def real(sec, w, now, title):
        src = f"plan real \u00b7 {fmt_age(w['age'])}"
        if w["resets_at"] is None:
            paint(sec, w["pct"], True, "\u2013", "reinicio desconocido", "", src, title)
            return
        t = w["resets_at"].astimezone()
        paint(sec, w["pct"], True, fmt_delta(w["resets_at"] - now), "para reiniciar",
              f"{DAYS[t.weekday()]} {t:%H:%M}", src, title)

    def apply(d):
        last["d"] = d
        footer, foot2 = ui["footer"], ui["foot2"]
        now = datetime.now(timezone.utc)
        rate, meta = d["rate"], d["rate_meta"]
        rb, rw, rf = rate.get("five_hour"), rate.get("seven_day"), rate.get("fable_week")

        if rb:  # plan real
            real(ui["block"], rb, now, "SESI\u00d3N 5H")
        else:
            pct, has, t2 = local_pct(d["block_work"], cfg["block_limit"], d["peak_block"])
            rem = d["block_remaining"]
            paint(ui["block"], pct, has, fmt_tok(d["block_work"]), "tokens",
                  f"reinicia en {fmt_delta(rem)}" if rem is not None else "sin sesi\u00f3n activa", t2,
                  "SESI\u00d3N 5H")

        if rw:
            real(ui["week"], rw, now, "SEMANA \u00b7 7D")
        else:
            pct, has, t2 = local_pct(d["week_work"], cfg["week_limit"], d["peak_week"])
            paint(ui["week"], pct, has, fmt_tok(d["week_work"]), "tokens", f"cache {fmt_tok(d['cache_week'])}", t2,
                  "SEMANA \u00b7 7D")

        if "fable" in ui:  # opcional: limite semanal independiente de Fable
            if rf:
                real(ui["fable"], rf, now, "FABLE \u00b7 7D")
            else:
                paint(ui["fable"], 0, False, "\u2013", "sin datos", "", "plan real \u00b7 sin dato", "FABLE \u00b7 7D")

        footer.config(text=f"actualizado {datetime.now():%H:%M:%S}")
        if rate:
            parts = [str(meta.get("plan") or "plan real")]
            parts += [f"{k} {v:.0f}%" for k, v in (meta.get("by_product") or {}).items()
                      if isinstance(v, (int, float)) and v]
            foot2.config(text=" \u00b7 ".join(parts), fg=MUTED)
        elif d["sources"] == 0:
            foot2.config(text="sin logs ni plan \u00b7 ver config", fg=TONES["warning"])
        else:
            foot2.config(text=f"plan: estimado \u00b7 {d['entries']} msgs locales", fg=MUTED)
        st = read_status()
        reason = st.get("reason")
        if reason == "login_required":
            foot2.config(text="sin sesi\u00f3n de claude.ai \u00b7 clic der. \u2192 Conectar (si Google falla, usa correo)",
                         fg=TONES["warning"])
        elif reason == "pywebview_missing":
            foot2.config(text="ejecuta `claude` e inicia sesi\u00f3n (o pip install pywebview)", fg=TONES["warning"])
        elif reason == "token_expired":
            foot2.config(text="token caducado \u00b7 abre `claude` una vez", fg=TONES["warning"])
        elif reason == "oauth_429":
            nxt = fstate["next"] or st.get("ts", 0) + (st.get("retry_after") or 0)
            when = f" {datetime.fromtimestamp(nxt):%H:%M}" if nxt > time.time() else ""
            foot2.config(text=f"uso limitado (429) \u00b7 reintenta{when}", fg=TONES["warning"])
        elif st.get("ok") is False and reason not in (None, "login_closed"):
            foot2.config(text=f"no pude leer claude.ai ({reason}) \u00b7 ver fetch.log", fg=TONES["warning"])

    # refresco en hilo aparte para no congelar la UI en la primera carga
    q, state, last = queue.Queue(), {"busy": False}, {}

    def worker():
        try:
            q.put(("ok", compute(cfg)))
        except Exception as ex:
            q.put(("err", str(ex)))

    def refresh():
        if not state["busy"]:
            state["busy"] = True
            threading.Thread(target=worker, daemon=True).start()

    hq = queue.Queue()
    start_hotkey(hq)
    start_tray(hq)

    def poll():
        try:
            while True:
                if hq.get_nowait() == "quit":
                    quit_app()
                else:
                    toggle_visible()
        except queue.Empty:
            pass
        try:
            while True:
                kind, d = q.get_nowait()
                state["busy"] = False
                if kind == "ok":
                    apply(d)
                else:
                    ui["footer"].config(text=("error: " + d)[:36])
        except queue.Empty:
            pass
        root.after(250, poll)

    def tick():
        refresh()
        fetch_tick()
        root.after(max(5, int(cfg["refresh_s"])) * 1000, tick)

    # lector del plan real (claude_usage_fetch.py): cada fetch_every_s, sin solaparse
    fstate = {"proc": None, "last": 0.0, "auto_login": False, "fails": 0, "retry": 0, "next": 0.0, "forced": 0.0}

    def fetch_tick(force=False, login=False):
        p = fstate["proc"]
        if p is not None and p.poll() is None:
            return
        if p is not None and not fstate.get("seen"):  # resultado del ultimo fetch: backoff exponencial si fallo
            fstate["seen"] = True
            st = read_status()
            fstate["fails"] = 0 if st.get("ok") else min(fstate["fails"] + 1, 4)
            ra = st.get("retry_after")
            fstate["retry"] = ra if not st.get("ok") and isinstance(ra, (int, float)) else 0
        every = fetch_delay(int(cfg["fetch_every_s"]), fstate["fails"], fstate["retry"])
        fstate["next"] = fstate["last"] + every if every > 0 else 0.0
        if force and time.time() - fstate["forced"] < 60:  # forzar: maximo una vez por minuto
            return
        if not force and (every <= 0 or time.time() - fstate["last"] < every):
            return
        if force:
            fstate["forced"] = time.time()
        reason = read_status().get("reason")
        if not force and reason == "login_required":
            if fstate["auto_login"]:  # la ventana de login se ofrece sola una vez por arranque
                return
            fstate["auto_login"], login = True, True
        fstate["last"], fstate["seen"] = time.time(), False
        fstate["next"] = 0.0
        fstate["proc"] = spawn_fetch(login)

    # arrastrar + menu (hamburguesa y clic derecho) en todos los widgets
    drag = {}

    def start_drag(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def do_drag(e):
        if not cfg["locked"]:
            root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    def end_drag(e):
        if not cfg["locked"]:
            cfg["x"], cfg["y"] = root.winfo_x(), root.winfo_y()
            save_cfg(cfg)

    def wheel(e):
        up = e.num == 4 or getattr(e, "delta", 0) > 0
        resize(rs["target"] * (1.1 if up else 1 / 1.1))

    def bind_all(w):
        for ev in ("<Control-MouseWheel>", "<Control-Button-4>", "<Control-Button-5>"):
            w.bind(ev, wheel)
        if str(w.cget("cursor")) != "hand2":  # hamburguesa y cerrar son clicables, no arrastran
            w.bind("<Button-1>", start_drag)
            w.bind("<B1-Motion>", do_drag)
            w.bind("<ButtonRelease-1>", end_drag)
            w.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        for ch in w.winfo_children():
            bind_all(ch)

    # opciones persistentes del menu
    v_top = tk.BooleanVar(value=bool(cfg["topmost"]))
    v_lock = tk.BooleanVar(value=bool(cfg["locked"]))
    v_compact = tk.BooleanVar(value=bool(cfg["compact"]))
    v_fable = tk.BooleanVar(value=bool(cfg["show_fable"]))
    v_alerts = tk.BooleanVar(value=bool(cfg["alerts"]))
    v_auto = tk.BooleanVar(value=autostart_get())
    v_alpha = tk.DoubleVar(value=round(float(cfg["opacity"]), 2))

    def quit_app():
        if root.state() != "withdrawn" and not cfg["locked"]:
            cfg["x"], cfg["y"] = root.winfo_x(), root.winfo_y()
        cfg["scale"] = round(sc["v"], 3)
        save_cfg(cfg)
        root.destroy()

    def rebuild():
        build()
        if "d" in last:
            apply(last["d"])
        relayout()

    def anchor(cx_, cy_):
        root.update_idletasks()
        w, h, m = root.winfo_width(), root.winfo_height(), px(24)
        x = root.winfo_screenwidth() - w - m if cx_ else m
        y = root.winfo_screenheight() - h - px(48) - m if cy_ else m
        root.geometry(f"+{x}+{y}")
        cfg["x"], cfg["y"] = x, y
        save_cfg(cfg)

    def toggle_visible():
        if root.state() == "withdrawn":
            root.deiconify()
            root.attributes("-topmost", bool(cfg["topmost"]))
        else:
            root.withdraw()

    def set_opt(key, var):
        cfg[key] = var.get()
        if key == "topmost":
            root.attributes("-topmost", cfg[key])
        elif key == "opacity":
            root.attributes("-alpha", cfg[key])
        elif key in ("compact", "show_fable"):
            rebuild()
        save_cfg(cfg)

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="Actualizar ahora", command=lambda: (_file_cache.clear(), refresh()))
    menu.add_command(label="Actualizar plan real ahora", command=lambda: fetch_tick(force=True))
    menu.add_command(label="Conectar con claude.ai\u2026", command=lambda: fetch_tick(force=True, login=True))
    menu.add_separator()
    menu.add_checkbutton(label="Siempre visible (encima)", variable=v_top, command=lambda: set_opt("topmost", v_top))
    menu.add_checkbutton(label="Bloquear posici\u00f3n", variable=v_lock, command=lambda: set_opt("locked", v_lock))
    m_alpha = tk.Menu(menu, tearoff=0)
    for a_ in (1.0, 0.9, 0.8, 0.7, 0.5):
        m_alpha.add_radiobutton(label=f"{a_:.0%}", value=a_, variable=v_alpha, command=lambda: set_opt("opacity", v_alpha))
    menu.add_cascade(label="Opacidad", menu=m_alpha)
    m_size = tk.Menu(menu, tearoff=0)
    for z in (0.75, 1.0, 1.25, 1.5, 2.0):
        m_size.add_command(label=f"{z:.0%}", command=lambda z=z: resize(z, save=True))
    menu.add_cascade(label="Tama\u00f1o", menu=m_size)
    menu.add_checkbutton(label="Modo compacto", variable=v_compact, command=lambda: set_opt("compact", v_compact))
    menu.add_checkbutton(label="Mostrar gauge de Fable", variable=v_fable,
                         command=lambda: set_opt("show_fable", v_fable))
    m_anchor = tk.Menu(menu, tearoff=0)
    for label, cx_, cy_ in (("Arriba izquierda", 0, 0), ("Arriba derecha", 1, 0),
                            ("Abajo izquierda", 0, 1), ("Abajo derecha", 1, 1)):
        m_anchor.add_command(label=label, command=lambda cx_=cx_, cy_=cy_: anchor(cx_, cy_))
    menu.add_cascade(label="Anclar a esquina", menu=m_anchor)
    menu.add_separator()
    menu.add_checkbutton(label="Alertas al superar el umbral", variable=v_alerts,
                         command=lambda: set_opt("alerts", v_alerts))
    if sys.platform == "win32":
        menu.add_checkbutton(label="Iniciar con Windows", variable=v_auto, command=lambda: autostart_set(v_auto.get()))
        menu.add_command(label="Ocultar (Ctrl+Alt+U)", command=toggle_visible)
    menu.add_separator()
    menu.add_command(label="Cerrar", command=quit_app)

    # redimensionar: la escala sube/baja todo junto, asi el aspect ratio se conserva
    rs = {"pending": False, "target": sc["v"]}

    def relayout():
        root.update_idletasks()
        root.geometry(f"{ui['W']}x{root.winfo_reqheight()}")

    def resize(z, save=False):
        z = min(max(z, SCALE_MIN), SCALE_MAX)
        rs["target"] = z
        if save:
            apply_scale()
        elif not rs["pending"]:
            rs["pending"] = True
            root.after(40, apply_scale)

    def apply_scale():
        rs["pending"] = False
        if abs(rs["target"] - sc["v"]) > 0.005:
            sc["v"] = rs["target"]
            refont()
            rebuild()
        cfg["scale"] = round(sc["v"], 3)

    grip = tk.Label(root, text="\u25e2", font=fonts["meta"], fg=MUTED, bg=SURF, cursor="size_nw_se")
    grip.place(relx=1.0, rely=1.0, anchor="se", x=-1, y=-1)
    gs = {}

    def grip_down(e):
        gs["x"], gs["w0"], gs["w"] = e.x_root, ui["W"], ui["W"] / sc["v"]

    def grip_move(e):
        resize((gs["w0"] + e.x_root - gs["x"]) / gs["w"])

    def grip_up(e):
        apply_scale()
        save_cfg(cfg)

    grip.bind("<Button-1>", grip_down)
    grip.bind("<B1-Motion>", grip_move)
    grip.bind("<ButtonRelease-1>", grip_up)

    # el alto sale del contenido (aspect ratio fijo), sin salirse de la pantalla
    build()
    root.update_idletasks()
    W, H = ui["W"], root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x = cfg["x"] if cfg["x"] is not None else sw - W - px(24)
    y = cfg["y"] if cfg["y"] is not None else px(24)
    y = max(0, min(y, sh - H - px(48)))
    x = max(0, min(x, sw - W))
    root.geometry(f"{W}x{H}+{x}+{y}")

    poll()
    tick()
    root.mainloop()


if __name__ == "__main__":
    if "--fetch" in sys.argv:  # el .exe se relanza a si mismo como lector (sin python ni scripts sueltos)
        import claude_usage_fetch
        sys.exit(claude_usage_fetch.main(sys.argv[1:]))
    run_ui()
