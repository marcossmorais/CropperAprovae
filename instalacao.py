"""Instalação no Windows: usada pelo instalador (installer.py) e pelo próprio app (CropperAprovae.exe --desinstalar).

Instala só para o usuário atual (sem precisar de administrador):
- programa em %LOCALAPPDATA%\\Programs\\CropperAprovae
- dados em %LOCALAPPDATA%\\CropperAprovae (config.json, workspace, .venv-ai da IA); variável CROPPERAPROVAE_DATA troca isso
- atalhos na Área de Trabalho e no menu Iniciar; entrada em "Aplicativos instalados" (HKCU, com desinstalador)
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import winreg

import locais
from versao import VERSAO

NOME = "CropperAprovae"
EXE = "CropperAprovae.exe"
CHAVE = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CropperAprovae"
WEBVIEW2 = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"  # instalador oficial (Evergreen) da Microsoft
TORCH_CPU = "https://download.pytorch.org/whl/cpu"
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErroInstalacao(Exception):
    pass


def _local():
    return os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")


def data_dir():
    return locais.data_dir()


def dir_padrao():
    return os.path.join(_local(), "Programs", "CropperAprovae")


def _pasta_especial(csidl):
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return buf.value


def pasta_desktop():
    return _pasta_especial(0x10)  # CSIDL_DESKTOPDIRECTORY


def pasta_iniciar():
    return _pasta_especial(0x02)  # CSIDL_PROGRAMS (menu Iniciar do usuário)


def caixa(texto, titulo=NOME, sim_nao=False, padrao_nao=False):
    """MessageBox do Windows (ctypes: o .exe do app não precisa carregar o tkinter só para isso)."""
    flags = 0x20 | (0x4 if sim_nao else 0) | (0x100 if padrao_nao else 0)
    return ctypes.windll.user32.MessageBoxW(0, texto, titulo, flags)  # 6 = Sim, 7 = Não, 1 = OK


# ---------------------------------------------------------------- WebView2 (a janela do app)
def webview2_instalado():
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2}"),
                      (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2}"),
                      (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2}")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                pv = winreg.QueryValueEx(k, "pv")[0]
            if pv and pv != "0.0.0.0":
                return True
        except OSError:
            continue
    return False


def instalar_webview2(log):
    log("Baixando o WebView2 (instalador oficial da Microsoft, ~2 MB)…")
    dst = os.path.join(tempfile.gettempdir(), "MicrosoftEdgeWebview2Setup.exe")
    try:
        urllib.request.urlretrieve(WEBVIEW2_URL, dst)
        log("Instalando o WebView2…")
        subprocess.run([dst, "/silent", "/install"], check=False, creationflags=SEM_JANELA, timeout=600)
    except Exception as e:  # sem internet, proxy, etc.
        raise ErroInstalacao(f"Não consegui instalar o WebView2 ({e}). Baixe em "
                             "https://developer.microsoft.com/microsoft-edge/webview2/ e rode o instalador de novo.")
    finally:
        try:
            os.remove(dst)
        except OSError:
            pass
    if not webview2_instalado():
        raise ErroInstalacao("O WebView2 não ficou instalado. Instale-o manualmente e rode o instalador de novo.")


# ---------------------------------------------------------------- atalhos e registro
def criar_atalho(lnk, alvo, pasta_trabalho):
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:S4I_LNK);$s.TargetPath=$env:S4I_ALVO;"
          "$s.WorkingDirectory=$env:S4I_WD;$s.IconLocation=$env:S4I_ALVO+',0';"
          "$s.Description='Recorte e revisao de figuras das provas';$s.Save()")
    env = dict(os.environ, S4I_LNK=lnk, S4I_ALVO=alvo, S4I_WD=pasta_trabalho)
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True, creationflags=SEM_JANELA)


def registrar(dir_instalacao, atalhos):
    exe = os.path.join(dir_instalacao, EXE)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE) as k:
        for nome, valor in (("DisplayName", NOME), ("DisplayVersion", VERSAO), ("Publisher", "Marcos Morais"),
                            ("InstallLocation", dir_instalacao), ("DisplayIcon", exe),
                            ("UninstallString", f'"{exe}" --desinstalar')):
            winreg.SetValueEx(k, nome, 0, winreg.REG_SZ, valor)
        winreg.SetValueEx(k, "Atalhos", 0, winreg.REG_MULTI_SZ, list(atalhos))
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, int(os.path.getsize(exe) / 1024))


def _ler_registro():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE) as k:
            local = winreg.QueryValueEx(k, "InstallLocation")[0]
            try:
                atalhos = list(winreg.QueryValueEx(k, "Atalhos")[0])
            except OSError:
                atalhos = []
        return local, atalhos
    except OSError:
        return None, []


def versao_instalada():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE) as k:
            return winreg.QueryValueEx(k, "DisplayVersion")[0]
    except OSError:
        return None


# ---------------------------------------------------------------- IA opcional (Docling, no próprio venv)
def achar_python():
    """Python 3.10+ do computador (para criar o venv da IA). Devolve (comando, 'x.y') ou None."""
    teste = "import sys,venv,ensurepip;print(sys.version_info[0],sys.version_info[1])"
    for cmd in (["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"], ["py", "-3.10"], ["py", "-3"], ["python"], ["python3"]):
        try:
            r = subprocess.run(cmd + ["-c", teste], capture_output=True, text=True, timeout=30, creationflags=SEM_JANELA)
            maior, menor = (int(x) for x in r.stdout.split()[:2])
        except Exception:
            continue
        if r.returncode == 0 and (maior, menor) >= (3, 10):
            return cmd, f"{maior}.{menor}"
    return None


def _rodar(cmd, log):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=SEM_JANELA)
    for linha in p.stdout:
        linha = linha.strip()
        if linha:
            log("   " + linha[:160])
    if p.wait() != 0:
        raise ErroInstalacao("Falhou: " + " ".join(cmd[:6]) + " …")


def instalar_ia(log):
    py = achar_python()
    if not py:
        raise ErroInstalacao("A IA precisa do Python 3.10 ou mais novo, e não achei nenhum neste computador. Instale em "
                             "python.org (marque \"Add python.exe to PATH\") e rode o instalador de novo; o resto do "
                             "programa funciona sem a IA.")
    venv = os.path.join(data_dir(), ".venv-ai")
    log(f"IA: criando o ambiente com o Python {py[1]}…")
    _rodar(py[0] + ["-m", "venv", venv], log)
    vpy = os.path.join(venv, "Scripts", "python.exe")
    pip = [vpy, "-m", "pip", "install", "--progress-bar", "off", "--disable-pip-version-check"]
    teste = os.environ.get("S4I_IA_PACOTES")  # só para testar o mecanismo sem baixar 1,5 GB
    if teste:
        log("IA (teste): instalando " + teste)
        _rodar(pip + teste.split(), log)
    else:
        log("IA: baixando o PyTorch para CPU (~200 MB)…")
        _rodar(pip + ["torch", "torchvision", "--index-url", TORCH_CPU], log)
        log("IA: instalando o Docling e o PyMuPDF (pode levar alguns minutos)…")
        _rodar(pip + ["pymupdf", "docling"], log)
        log("IA: conferindo…")
        _rodar([vpy, "-c", "import torch, pymupdf; from docling.models.stages.layout.layout_model import LayoutModel; "
                           "print('ok')"], log)
    return vpy


# ---------------------------------------------------------------- instalar / desinstalar
def _config_atualizar(**novos):
    d = data_dir()
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg.update({k: v for k, v in novos.items() if v})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def instalar(exe_origem, destino, acervo=None, atalho_desktop=True, atalho_iniciar=True, ia=False, webview2=True,
             log=print):
    if not os.path.isfile(exe_origem):
        raise ErroInstalacao("O programa não está dentro deste instalador (arquivo corrompido?).")
    destino = os.path.normpath(destino)
    if webview2 and not webview2_instalado():
        instalar_webview2(log)
    elif webview2:
        log("WebView2 já instalado.")
    log(f"Copiando o programa para {destino}…")
    os.makedirs(destino, exist_ok=True)
    alvo = os.path.join(destino, EXE)
    tmp = alvo + ".novo"
    try:
        shutil.copyfile(exe_origem, tmp)
        os.replace(tmp, alvo)
    except PermissionError:
        raise ErroInstalacao(f"Não consegui gravar {EXE}: o {NOME} está aberto? Feche-o e tente de novo.")
    log("Gravando as configurações…")
    _config_atualizar(acervo=acervo)
    atalhos = []
    for ligado, pasta in ((atalho_desktop, pasta_desktop), (atalho_iniciar, pasta_iniciar)):
        if ligado:
            lnk = os.path.join(pasta(), NOME + ".lnk")
            criar_atalho(lnk, alvo, data_dir())
            atalhos.append(lnk)
            log("Atalho criado: " + lnk)
    registrar(destino, atalhos)
    log('Registrado em "Aplicativos instalados" (para desinstalar depois).')
    if ia:
        instalar_ia(log)
        log("IA instalada.")
    log("Pronto.")
    return alvo


def _apagar_depois(pasta):
    """O .exe em execução trava a própria pasta: um cmd separado apaga depois que ele sair."""
    if not os.path.isfile(os.path.join(pasta, EXE)) or len(os.path.normpath(pasta).split(os.sep)) < 3:
        return  # não parece a pasta de instalação: não apaga nada
    # string única (não lista): o Python reescreveria as aspas do caminho como \" e o cmd não entenderia
    subprocess.Popen(f'cmd /c ping -n 4 127.0.0.1 >nul & rmdir /s /q "{pasta}"', cwd=tempfile.gettempdir(),
                     creationflags=SEM_JANELA | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)


def desinstalar(silencioso=False, apagar_dados=None):
    local, atalhos = _ler_registro()
    if not local:
        if not silencioso:
            caixa(f"O {NOME} não está instalado por este instalador.")
        return False
    if not silencioso and caixa(f"Remover o {NOME} deste computador?", sim_nao=True) != 6:
        return False
    if apagar_dados is None:
        apagar_dados = (not silencioso) and caixa(
            f"Apagar também os dados do programa?\n\n{data_dir()}\n\nIsso inclui as provas já processadas ali e a IA "
            "instalada. Se a sua área de trabalho estiver em outra pasta (config.json), ela não é tocada.\n\n"
            "Na dúvida, escolha Não.", sim_nao=True, padrao_nao=True) == 6
    for lnk in atalhos:
        try:
            os.remove(lnk)
        except OSError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, CHAVE)
    except OSError:
        pass
    if apagar_dados:
        os.chdir(tempfile.gettempdir())
        shutil.rmtree(data_dir(), ignore_errors=True)
    _apagar_depois(local)
    if not silencioso:
        caixa(f"O {NOME} foi removido.")
    return True
