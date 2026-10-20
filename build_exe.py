"""Gera dist/System4Image.exe (um arquivo só, sem console) com PyInstaller.

Uso: python build_exe.py      (precisa de: pip install pyinstaller pywebview pymupdf)
A IA (Docling) não vai dentro do .exe: ela continua no venv separado .venv-ai (ver README).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEP = os.pathsep  # ";" no Windows
# venv limpo só com pymupdf + pywebview + pyinstaller: no Python principal o PyInstaller arrasta o PyTorch (~400 MB)
BUILD_PY = os.path.join(HERE, ".venv-build", "Scripts", "python.exe")
if not os.path.isfile(BUILD_PY):
    sys.exit("crie o venv: python -m venv .venv-build && "
             ".venv-build/Scripts/pip install pymupdf==<mesma versão do Python principal> pywebview pyinstaller")

args = [
    BUILD_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "System4Image", "--icon", os.path.join(HERE, "static", "app.ico"),
    "--add-data", f"{os.path.join(HERE, 'static')}{SEP}static",
    "--add-data", f"{os.path.join(HERE, 'layout_ai.py')}{SEP}.",
    "--collect-submodules", "webview",
    "--hidden-import", "clr",
    os.path.join(HERE, "app.py"),
]
subprocess.run(args, check=True, cwd=HERE)
exe = os.path.join(HERE, "dist", "System4Image.exe")
print(f"pronto: {exe} ({os.path.getsize(exe) / 1e6:.0f} MB)")
