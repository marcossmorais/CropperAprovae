"""Gera dist/CropperAprovae-Setup.exe: o instalador, com o CropperAprovae.exe dentro.

Uso: python build_exe.py && python build_instalador.py   (usa o venv limpo .venv-build, como o build_exe.py)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_PY = os.path.join(HERE, ".venv-build", "Scripts", "python.exe")
APP = os.path.join(HERE, "dist", "CropperAprovae.exe")
if not os.path.isfile(BUILD_PY) or not os.path.isfile(APP):
    sys.exit("rode antes o build_exe.py (precisa do .venv-build e do dist/CropperAprovae.exe)")
subprocess.run([BUILD_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                "--name", "CropperAprovae-Setup", "--icon", os.path.join(HERE, "static", "app.ico"),
                "--add-data", f"{APP}{os.pathsep}.", "--add-data", f"{os.path.join(HERE, 'static', 'app.ico')}{os.pathsep}.",
                "--distpath", os.path.join(HERE, "dist"), "--workpath", os.path.join(HERE, "build", "setup"),
                "--specpath", os.path.join(HERE, "build"), os.path.join(HERE, "installer.py")], check=True, cwd=HERE)
setup = os.path.join(HERE, "dist", "CropperAprovae-Setup.exe")
print(f"pronto: {setup} ({os.path.getsize(setup) / 1e6:.0f} MB)")
