"""Onde ficam as coisas, rodando do código-fonte ou do .exe (PyInstaller).

- APP_DIR: arquivos do programa (static/, layout_ai.py). No .exe é a pasta temporária que ele extrai.
- DATA_DIR: o que o programa grava (config.json, app.log). Do código-fonte é a pasta do repositório;
  do .exe é %LOCALAPPDATA%\\CropperAprovae.
- WORKSPACE: área de trabalho dos recortes. Padrão DATA_DIR/workspace; config.json pode apontar outra
  ("workspace"), por exemplo a do repositório, para o .exe e o código-fonte enxergarem as mesmas provas.
- ROOT: acervo do System 3.0 (variável SYSTEM3_ROOT, ou "acervo" no config.json, ou Documents/CLAUDE/System3.0).
- ia_python(): Python do venv da IA (.venv-ai), ou "ia_python" no config.json.
"""
import json
import os
import sys

import locais  # noqa: E402

FROZEN = getattr(sys, "frozen", False)
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.dirname(os.path.abspath(__file__)) if not FROZEN else os.path.dirname(sys.executable)
DATA_DIR = (os.environ.get("CROPPERAPROVAE_DATA")
            or (locais.data_dir() if FROZEN else os.path.dirname(os.path.abspath(__file__))))
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG = os.path.join(DATA_DIR, "config.json")


def settings():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_s = settings()
WORKSPACE = _s.get("workspace") or os.path.join(DATA_DIR, "workspace")
ROOT = (os.environ.get("SYSTEM3_ROOT") or _s.get("acervo")
        or os.path.join(os.path.expanduser("~"), "Documents", "CLAUDE", "System3.0"))


def ia_python():
    cands = [settings().get("ia_python"), os.path.join(SRC_DIR, ".venv-ai", "Scripts", "python.exe"),
             os.path.join(DATA_DIR, ".venv-ai", "Scripts", "python.exe")]
    return next((c for c in cands if c and os.path.isfile(c)), None)
