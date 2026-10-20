"""Instalador do CropperAprovae (janela pequena): copia o programa, cria atalhos e, se você quiser, instala a IA.

Uso: CropperAprovae-Setup.exe            (janela)
     CropperAprovae-Setup.exe --silencioso [--dir PASTA] [--acervo PASTA] [--sem-atalhos] [--ia] [--log ARQUIVO]
"""
import argparse
import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import instalacao as ins
from versao import VERSAO

APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
EXE_ORIGEM = os.path.join(APP_DIR, ins.EXE) if getattr(sys, "frozen", False) else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dist", ins.EXE)
ICONE = os.path.join(APP_DIR, "app.ico") if getattr(sys, "frozen", False) else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "app.ico")
ACERVO_PADRAO = os.path.join(os.path.expanduser("~"), "Documents", "CLAUDE", "System3.0")


class Janela(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Instalador — {ins.NOME}")
        self.resizable(False, False)
        try:
            self.iconbitmap(ICONE)
        except tk.TclError:
            pass
        self.fila = queue.Queue()
        self.exe_instalado = None
        self.v_dir = tk.StringVar(value=ins.dir_padrao())
        self.v_acervo = tk.StringVar(value=ACERVO_PADRAO if os.path.isdir(os.path.join(ACERVO_PADRAO, "Documentos")) else "")
        self.v_desktop = tk.BooleanVar(value=True)
        self.v_iniciar = tk.BooleanVar(value=True)
        self.v_ia = tk.BooleanVar(value=False)
        corpo = ttk.Frame(self, padding=18)
        corpo.grid()
        ttk.Label(corpo, text=ins.NOME, font=("Segoe UI", 15, "bold")).grid(sticky="w")
        anterior = ins.versao_instalada()
        sub = f"Versão {VERSAO}" + (f" · atualiza a versão {anterior} já instalada" if anterior else "")
        ttk.Label(corpo, text=sub, foreground="#666").grid(sticky="w", pady=(0, 12))
        self.pag_opcoes = ttk.Frame(corpo)
        self.pag_prog = ttk.Frame(corpo)
        self._opcoes(self.pag_opcoes)
        self._progresso(self.pag_prog)
        self.pag_opcoes.grid(sticky="nsew")
        self.after(150, self._detectar)

    # ------------------------------------------------------------- páginas
    def _linha(self, pai, rotulo, var, linha):
        ttk.Label(pai, text=rotulo).grid(row=linha, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Entry(pai, textvariable=var, width=62).grid(row=linha + 1, column=0, sticky="we")
        ttk.Button(pai, text="Procurar…", command=lambda: self._escolher(var)).grid(row=linha + 1, column=1, padx=(6, 0))

    def _opcoes(self, p):
        self._linha(p, "Instalar em:", self.v_dir, 0)
        self._linha(p, "Pasta com as provas (a que tem a pasta Documentos; pode deixar em branco e escolher depois):",
                    self.v_acervo, 2)
        ttk.Checkbutton(p, text="Atalho na Área de Trabalho", variable=self.v_desktop).grid(row=4, sticky="w", pady=(12, 0))
        ttk.Checkbutton(p, text="Atalho no menu Iniciar", variable=self.v_iniciar).grid(row=5, sticky="w")
        self.cb_ia = ttk.Checkbutton(p, text="Instalar também a IA de segunda opinião (opcional, ~1,5 GB, precisa de internet)",
                                     variable=self.v_ia, state="disabled")
        self.cb_ia.grid(row=6, columnspan=2, sticky="w", pady=(10, 0))
        self.l_ia = ttk.Label(p, text="Procurando o Python…", foreground="#666")
        self.l_ia.grid(row=7, columnspan=2, sticky="w", padx=(22, 0))
        bt = ttk.Frame(p)
        bt.grid(row=8, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(bt, text="Cancelar", command=self.destroy).grid(row=0, column=0, padx=6)
        self.b_instalar = ttk.Button(bt, text="Instalar", command=self._instalar)
        self.b_instalar.grid(row=0, column=1)

    def _progresso(self, p):
        self.barra = ttk.Progressbar(p, mode="indeterminate", length=520)
        self.barra.grid(sticky="we")
        self.texto = tk.Text(p, width=74, height=14, state="disabled", font=("Consolas", 9), wrap="none", relief="flat",
                             background="#f4f4f4")
        self.texto.grid(pady=(10, 0))
        self.bt_fim = ttk.Frame(p)
        self.bt_fim.grid(sticky="e", pady=(14, 0))
        self.b_abrir = ttk.Button(self.bt_fim, text=f"Abrir o {ins.NOME}", command=self._abrir, state="disabled")
        self.b_abrir.grid(row=0, column=0, padx=6)
        self.b_fechar = ttk.Button(self.bt_fim, text="Fechar", command=self.destroy, state="disabled")
        self.b_fechar.grid(row=0, column=1)

    # ------------------------------------------------------------- ações
    def _escolher(self, var):
        pasta = filedialog.askdirectory(initialdir=var.get() or os.path.expanduser("~"), parent=self)
        if pasta:
            var.set(os.path.normpath(pasta))

    def _detectar(self):
        py = ins.achar_python()
        if py:
            self.l_ia.config(text=f"Python {py[1]} encontrado.")
            self.cb_ia.config(state="normal")
        else:
            self.l_ia.config(text="Python 3.10+ não encontrado (python.org). Sem ele a IA fica de fora; o resto funciona.")

    def _log(self, msg):
        self.fila.put(("log", msg))

    def _instalar(self):
        dest = self.v_dir.get().strip()
        if not dest:
            return messagebox.showwarning(ins.NOME, "Escolha a pasta de instalação.", parent=self)
        acervo = self.v_acervo.get().strip() or None
        if acervo and not os.path.isdir(acervo):
            return messagebox.showwarning(ins.NOME, "A pasta das provas não existe. Corrija ou deixe em branco.", parent=self)
        args = dict(exe_origem=EXE_ORIGEM, destino=dest, acervo=acervo, atalho_desktop=self.v_desktop.get(),
                    atalho_iniciar=self.v_iniciar.get(), ia=self.v_ia.get(), log=self._log)
        self.pag_opcoes.grid_remove()
        self.pag_prog.grid(sticky="nsew")
        self.barra.start(12)

        def trabalho():
            try:
                self.fila.put(("fim", ins.instalar(**args)))
            except ins.ErroInstalacao as e:
                self.fila.put(("erro", str(e)))
            except Exception as e:  # inesperado: mostra, não fecha calado
                self.fila.put(("erro", f"{type(e).__name__}: {e}"))

        threading.Thread(target=trabalho, daemon=True).start()
        self.after(100, self._ler_fila)

    def _escrever(self, linha):
        self.texto.config(state="normal")
        self.texto.insert("end", linha + "\n")
        self.texto.see("end")
        self.texto.config(state="disabled")

    def _ler_fila(self):
        try:
            while True:
                tipo, dado = self.fila.get_nowait()
                if tipo == "log":
                    self._escrever(dado)
                elif tipo == "fim":
                    self.barra.stop()
                    self.barra.config(mode="determinate", value=100)
                    self.exe_instalado = dado
                    self._escrever("\n✓ Instalação concluída.")
                    self.b_abrir.config(state="normal")
                    self.b_fechar.config(state="normal")
                    return
                elif tipo == "erro":
                    self.barra.stop()
                    self._escrever("\n✕ " + dado)
                    self.b_fechar.config(state="normal")
                    messagebox.showerror(ins.NOME, dado, parent=self)
                    return
        except queue.Empty:
            pass
        self.after(100, self._ler_fila)

    def _abrir(self):
        if self.exe_instalado:
            os.startfile(self.exe_instalado)  # noqa: S606
        self.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--silencioso", action="store_true")
    ap.add_argument("--dir", default=ins.dir_padrao())
    ap.add_argument("--acervo")
    ap.add_argument("--sem-atalhos", action="store_true")
    ap.add_argument("--ia", action="store_true")
    ap.add_argument("--sem-webview2", action="store_true")
    ap.add_argument("--log")
    a = ap.parse_args()
    if a.silencioso:
        arq = open(a.log, "w", encoding="utf-8", buffering=1) if a.log else sys.stdout
        try:
            ins.instalar(EXE_ORIGEM, a.dir, a.acervo, not a.sem_atalhos, not a.sem_atalhos, a.ia, not a.sem_webview2,
                         log=lambda m: print(m, file=arq))
        except ins.ErroInstalacao as e:
            print("ERRO: " + str(e), file=arq)
            sys.exit(1)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # texto nítido em telas com escala
    except Exception:
        pass
    Janela().mainloop()


if __name__ == "__main__":
    main()
