"""Cria o atalho "CropperAprovae" na Área de Trabalho (e no menu Iniciar), abrindo app.py sem terminal.

Uso: python criar_atalho.py
"""
import os
import struct
import subprocess
import sys

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "static", "app.ico")


def make_icon():
    """Ícone 256px (PNG dentro de um .ico, aceito desde o Windows Vista), desenhado com PyMuPDF."""
    doc = pymupdf.open()
    pg = doc.new_page(width=256, height=256)
    blue = (0.145, 0.388, 0.922)
    pg.draw_rect(pymupdf.Rect(8, 8, 248, 248), color=blue, fill=blue, radius=0.18)
    white = (1, 1, 1)
    # "página" com uma área recortada
    pg.draw_rect(pymupdf.Rect(62, 44, 194, 212), color=white, fill=white, radius=0.06)
    for y in (70, 88, 106):
        pg.draw_line((80, y), (176, y), color=blue, width=7)
    pg.draw_rect(pymupdf.Rect(80, 126, 176, 192), color=(0.96, 0.62, 0.04), width=8, dashes="[14 8] 0")
    png = pg.get_pixmap(alpha=True, clip=pg.rect).tobytes("png")
    head = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    with open(ICO, "wb") as f:
        f.write(head + png)


def shortcut(path, target, args, workdir, icon):
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);$s.TargetPath=$env:TGT;"
          "$s.Arguments=$env:ARGS;$s.WorkingDirectory=$env:WD;$s.IconLocation=$env:ICO;"
          "$s.Description='Recorte e revisao de figuras das provas';$s.Save()")
    env = dict(os.environ, LNK=path, TGT=target, ARGS=args, WD=workdir, ICO=icon)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True)


def main():
    if sys.platform != "win32":
        sys.exit("só para Windows")
    make_icon()
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "[Environment]::GetFolderPath('Desktop');[Environment]::GetFolderPath('Programs')"],
                         capture_output=True, text=True).stdout.splitlines()
    app = os.path.join(HERE, "app.py")
    for folder in filter(None, (x.strip() for x in out)):
        antigo = os.path.join(folder, "System 4.0 Image.lnk")  # nome até a v1.0.x
        if os.path.exists(antigo):
            os.remove(antigo)
        lnk = os.path.join(folder, "CropperAprovae.lnk")
        shortcut(lnk, pyw, f'"{app}"', HERE, ICO)
        print("atalho criado:", lnk)


if __name__ == "__main__":
    main()
