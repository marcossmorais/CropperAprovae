"""CropperAprovae como aplicativo: janela própria do Windows, sem terminal e sem navegador.

Uso: pythonw app.py   (ou o atalho criado por criar_atalho.bat)
- Sobe o servidor numa porta livre dentro do próprio processo e abre uma janela nativa (pywebview + WebView2,
  que já vem no Windows 10/11). Fechou a janela, fechou tudo.
- Sem pywebview instalado, abre no navegador padrão (como o server.py).
"""
import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if "--desinstalar" in sys.argv:  # chamado por "Aplicativos instalados"; antes de qualquer coisa que crie ou abra arquivos
    import instalacao
    instalacao.desinstalar(silencioso="--silencioso" in sys.argv, apagar_dados=True if "--apagar-dados" in sys.argv else None)
    sys.exit(0)

import paths  # noqa: E402

os.chdir(paths.DATA_DIR)

# pythonw não tem console: guarda a saída num log para diagnosticar erros
if sys.stdout is None or sys.stderr is None:
    _log = open(os.path.join(paths.DATA_DIR, "app.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

import server  # noqa: E402


def free_port(preferida=8765):
    for p in (preferida, 0):
        s = socket.socket()
        try:
            if sys.platform == "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", p))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("sem porta livre")


def main():
    port = free_port()
    srv = server.make_server(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    print(f"CropperAprovae em {url}")
    try:
        import webview
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        print("pywebview não instalado: abrindo no navegador (pip install pywebview para janela própria)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return
    webview.create_window("CropperAprovae", url, width=1600, height=960, min_size=(900, 600), maximized=True,
                          text_select=True)
    webview.start(private_mode=False, storage_path=os.path.join(paths.DATA_DIR, ".webview"))
    srv.shutdown()


if __name__ == "__main__":
    main()
