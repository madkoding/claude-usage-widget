"""Statusline de Claude Code -> guarda el % REAL del plan (5h y 7d) para el widget.

Claude Code ejecuta este script en cada actualizacion de la statusline y le pasa un JSON por stdin.
Solo hay `rate_limits` para suscriptores Pro/Max y tras la primera respuesta de la sesion.

Configuracion en settings.json de Claude Code (campo "statusLine"):
  Windows:  {"type": "command", "command": "python C:/ruta/al/widget/claude_usage_statusline.py"}
            (en Windows Claude Code usa Git Bash si esta instalado: escribe la ruta con barras normales, no invertidas)
  WSL:      {"type": "command", "command": "python3 /mnt/c/ruta/al/widget/claude_usage_statusline.py"}

Escribe %APPDATA%/claude-usage-widget-motita/rate_limits.json (desde WSL detecta la carpeta de Windows;
CLAUDE_USAGE_WIDGET_DIR fuerza otra ruta) e imprime una linea corta para la propia statusline.
"""
import glob
import json
import os
import sys
import time


def out_dir():
    forced = os.environ.get("CLAUDE_USAGE_WIDGET_DIR")
    if forced:
        return forced
    if os.environ.get("APPDATA"):
        return os.path.join(os.environ["APPDATA"], "claude-usage-widget-motita")
    if os.path.isdir("/mnt/c/Users"):
        skip = {"public", "default", "default user", "all users"}
        for u in sorted(glob.glob("/mnt/c/Users/*/AppData/Roaming")):
            if u.split("/")[4].lower() not in skip:
                return os.path.join(u, "claude-usage-widget-motita")
    return os.path.join(os.path.expanduser("~"), ".config", "claude-usage-widget-motita")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    rl = data.get("rate_limits") or {}
    keep = {k: rl[k] for k in ("five_hour", "seven_day") if isinstance(rl.get(k), dict)}
    if keep:
        try:
            d = out_dir()
            os.makedirs(d, exist_ok=True)
            target = os.path.join(d, "rate_limits.json")
            try:  # fusionar: conserva fable_week / by_product / plan que escribe el lector de claude.ai
                with open(target, encoding="utf-8") as f:
                    cur = json.load(f)
                if not isinstance(cur, dict):
                    cur = {}
            except Exception:
                cur = {}
            cur.update(keep)
            cur["updated"] = time.time()
            tmp = target + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f)
            os.replace(tmp, target)
        except Exception:
            pass
    parts = []
    model = (data.get("model") or {}).get("display_name")
    if model:
        parts.append(model)
    for k, lab in (("five_hour", "5h"), ("seven_day", "7d")):
        v = (rl.get(k) or {}).get("used_percentage")
        if v is not None:
            parts.append(f"{lab} {float(v):.0f}%")
    print(" \u00b7 ".join(parts))


main()
