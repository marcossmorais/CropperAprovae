"""CropperAprovae — servidor local da tela de revisão (só biblioteca padrão + PyMuPDF).

Uso: python server.py [--port 8765] [--no-browser]   (para abrir como app: python app.py)
Abre http://127.0.0.1:8765 — importar PDF, revisar recortes em miniatura, recortar manualmente,
exportar aprovados para Documentos/<Banca>/<Ano>/imagens + JSON (com backup).
"""
import datetime
import importlib
import json
import mimetypes
import os
import subprocess
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths  # noqa: E402
import engine  # noqa: E402

STATIC = os.path.join(paths.APP_DIR, "static")
BACKUP_ROOT = os.path.join(engine.ROOT, "logs", "system4_image", "backup")
SKIP = {".tmp.driveupload", "backup", "legado", "legado_duplicatas", "legado_antigo", "quarentena", "_quarentena",
        "originais_backup", "_quarentena_imagens", "revisao", "md", "imagens", "extracao"}
LOCK = threading.Lock()
_pdf_cache = {"list": None}
CONFIG = paths.CONFIG


def get_config():
    try:
        cfg = json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        cfg = {}
    if not cfg.get("pasta") or not os.path.isdir(cfg["pasta"]):
        cfg["pasta"] = engine.DOCS
    cfg.setdefault("recentes", [])
    return cfg


def set_config(**kw):
    cfg = get_config()
    cfg.update(kw)
    if kw.get("pasta"):
        cfg["recentes"] = [kw["pasta"]] + [p for p in cfg["recentes"] if p != kw["pasta"] and os.path.isdir(p)][:7]
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG)
    return cfg


def pick_folder(initial):
    """Seletor de pasta nativo do Windows, em outro processo (PowerShell + WinForms) para não travar o servidor.
    Funciona igual do código-fonte e do .exe (que não tem um python.exe para abrir o tkinter)."""
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true;ShowInTaskbar=$false};"
          "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
          "$d.Description='Escolha a pasta com as provas';$d.SelectedPath=$env:S4I_INICIAL;"
          "if($d.ShowDialog($o) -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;$d.SelectedPath}")
    out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                         encoding="utf-8", env=dict(os.environ, S4I_INICIAL=initial or ""),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    p = out.stdout.strip()
    return os.path.normpath(p) if p else None


def manifests():
    out = {}
    if os.path.isdir(engine.WORKSPACE):
        for pid in os.listdir(engine.WORKSPACE):
            mp = os.path.join(engine.WORKSPACE, pid, "manifest.json")
            if os.path.exists(mp):
                try:
                    out[pid] = summary(json.load(open(mp, encoding="utf-8")))
                except Exception:
                    pass
    return out


def library(pasta):
    """PDFs da pasta escolhida (recursivo), agrupados por subpasta, com o estado de cada um."""
    ms = manifests()
    groups = {}
    for r, ds, fs in os.walk(pasta):
        ds[:] = sorted(d for d in ds if d.lower() not in SKIP and not d.startswith("."))
        for f in sorted(fs, key=str.lower):
            if not f.lower().endswith(".pdf") or "gabarito" in f.lower():
                continue
            p = os.path.normpath(os.path.join(r, f))
            jp = engine.find_json_for_pdf(p)
            item = {"path": p, "nome": f, "json": os.path.basename(jp) if jp else None,
                    "prova_id": engine.prova_id_from_pdf(p)}
            if jp:
                try:
                    d = json.load(open(jp, encoding="utf-8"))
                    qs = d.get("questions") if isinstance(d, dict) else d
                    item["questoes"] = len(qs or [])
                    item["com_imagem"] = sum(1 for q in qs or [] if q.get("has_image"))
                except Exception:
                    item["json_erro"] = True
            m = ms.get(item["prova_id"])
            if m and os.path.normcase(m["pdf"]) == os.path.normcase(p):
                item["ws"] = m
            rel = os.path.relpath(r, pasta).replace(os.sep, "/")
            groups.setdefault("" if rel == "." else rel, []).append(item)
    return [{"pasta": k, "itens": v} for k, v in sorted(groups.items(), key=lambda kv: kv[0].lower())]


def allowed_pdf(pdf):
    pdf = os.path.realpath(pdf)
    roots = [engine.ROOT, get_config()["pasta"]]
    return os.path.isfile(pdf) and any(pdf.startswith(os.path.realpath(r)) for r in roots)


def list_pdfs():
    if _pdf_cache["list"] is None:
        out = []
        for r, ds, fs in os.walk(engine.DOCS):
            ds[:] = [d for d in ds if d.lower() not in SKIP and not d.startswith(".")]
            for f in fs:
                if f.lower().endswith(".pdf") and "gabarito" not in f.lower():
                    p = os.path.join(r, f)
                    out.append({"path": p, "rel": os.path.relpath(p, engine.DOCS).replace(os.sep, "/")})
        out.sort(key=lambda x: x["rel"].lower())
        _pdf_cache["list"] = out
    done = {d for d in os.listdir(engine.WORKSPACE)} if os.path.isdir(engine.WORKSPACE) else set()
    for x in _pdf_cache["list"]:
        x["processada"] = engine.prova_id_from_pdf(x["path"]) in done
        x["json"] = bool(engine.find_json_for_pdf(x["path"]))
    return _pdf_cache["list"]


def wdir(pid):
    d = os.path.realpath(os.path.join(engine.WORKSPACE, pid))
    if not d.startswith(os.path.realpath(engine.WORKSPACE)) or not os.path.isdir(d):
        raise ValueError("prova inexistente")
    return d


# ---------------------------------------------------------------- segunda opinião (IA de layout, Docling)
IA_SCRIPT = os.path.join(paths.APP_DIR, "layout_ai.py")
IA_FILE = "ia_docling.json"
_ia_procs = {}


def ia_available():
    return bool(paths.ia_python()) and os.path.isfile(IA_SCRIPT)


def ia_run(pid, force=False):
    """Roda o Docling na prova inteira em outro processo (venv separado), sem travar a tela."""
    if not ia_available():
        return False
    pr = _ia_procs.get(pid)
    if pr and pr.poll() is None:
        return True
    out = os.path.join(wdir(pid), IA_FILE)
    if os.path.exists(out) and not force:
        return True
    m = load(pid)
    log = open(os.path.join(wdir(pid), "ia.log"), "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    _ia_procs[pid] = subprocess.Popen([paths.ia_python(), IA_SCRIPT, m["pdf"], "--backend", "docling",
                                       "--out", out], stdout=log, stderr=subprocess.STDOUT, env=env, cwd=paths.DATA_DIR,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                      | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    return True


def ia_status(pid):
    d = wdir(pid)
    out = os.path.join(d, IA_FILE)
    pr = _ia_procs.get(pid)
    if pr and pr.poll() is None:
        prog = None
        try:
            prog = open(out + ".progress").read().strip()
        except OSError:
            pass
        return {"status": "rodando", "progresso": prog}
    if os.path.exists(out):
        r = json.load(open(out, encoding="utf-8"))
        r["status"] = "pronto"
        return r
    if pr and pr.returncode:
        try:
            tail = open(os.path.join(d, "ia.log"), encoding="utf-8", errors="replace").read()[-600:]
        except OSError:
            tail = ""
        return {"status": "erro", "log": tail}
    return {"status": "nenhum" if ia_available() else "indisponivel"}


def load(pid):
    return json.load(open(os.path.join(wdir(pid), "manifest.json"), encoding="utf-8"))


def summary(m):
    cs = m["crops"]
    return {
        "prova_id": m["prova_id"], "pdf": m["pdf"], "json": m.get("json"), "banca": m.get("banca"), "ano": m.get("ano"),
        "recortes": len(cs), "aprovados": sum(c["status"] == "aprovado" for c in cs),
        "rejeitados": sum(c["status"] == "rejeitado" for c in cs), "pendentes": sum(c["status"] == "pendente" for c in cs), "herdados": sum(c["status"] == "herdado" for c in cs),
        "alertas": sum(1 for q in m["questions"].values() if q["warnings"]), "criado_em": m.get("criado_em"),
        "exportados": sum(bool(c.get("exportado")) for c in cs),
        "ultima_exportacao": (m.get("exportacoes") or [None])[-1],
    }


_engine_mtime = [None if paths.FROZEN else os.path.getmtime(engine.__file__)]


def reload_engine():
    """Recarrega o motor se o código mudou (evita exportar com versão antiga em memória). No .exe não há o que vigiar."""
    global engine
    if paths.FROZEN:
        return
    mt = os.path.getmtime(engine.__file__)
    if mt != _engine_mtime[0]:
        with LOCK:
            engine = importlib.reload(engine)
            _engine_mtime[0] = mt
            print("motor recarregado")


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self.send(200, open(os.path.join(STATIC, "index.html"), "rb").read(), "text/html; charset=utf-8")
            if u.path == "/api/pdfs":
                return self.send(200, list_pdfs())
            if u.path == "/api/config":
                return self.send(200, get_config())
            if u.path == "/api/library":
                pasta = qs.get("pasta", [get_config()["pasta"]])[0]
                if not os.path.isdir(pasta):
                    return self.send(400, {"erro": "pasta não existe: " + pasta})
                return self.send(200, {"pasta": pasta, "grupos": library(pasta)})
            if u.path == "/api/jsonq":
                m = load(qs["prova"][0])
                d = json.load(open(m["json"], encoding="utf-8"))
                n = int(qs["n"][0])
                q = next((q for q in d["questions"] if q.get("source_number") == n), None)
                return self.send(200, q or {"erro": "questão não está no JSON"})
            if u.path == "/api/provas":
                out = []
                if os.path.isdir(engine.WORKSPACE):
                    for pid in sorted(os.listdir(engine.WORKSPACE)):
                        mp = os.path.join(engine.WORKSPACE, pid, "manifest.json")
                        if os.path.exists(mp):
                            out.append(summary(json.load(open(mp, encoding="utf-8"))))
                return self.send(200, out)
            if u.path == "/api/ia":
                return self.send(200, ia_status(qs["prova"][0]))
            if u.path == "/api/manifest":
                pid_ = qs["prova"][0]
                m_ = load(pid_)
                if engine.ensure_layout(m_):  # manifests antigos: âncoras para a tela atribuir as caixas da IA
                    with LOCK:
                        engine.save_manifest(wdir(pid_), m_)
                return self.send(200, m_)
            if u.path == "/api/manifest_raw":
                return self.send(200, load(qs["prova"][0]))
            if u.path.startswith("/ws/"):
                _, _, pid, *rest = u.path.split("/")
                pid = urllib.parse.unquote(pid)
                f = os.path.realpath(os.path.join(wdir(pid), *[urllib.parse.unquote(x) for x in rest]))
                if not f.startswith(wdir(pid)) or not os.path.isfile(f):
                    return self.send(404, {"erro": "arquivo não encontrado"})
                return self.send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
            return self.send(404, {"erro": "rota não encontrada"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"erro": str(e)})

    def do_POST(self):
        reload_engine()
        try:
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            if u.path == "/api/upload":
                name = os.path.basename(qs.get("name", ["prova.pdf"])[0])
                if not name.lower().endswith(".pdf"):
                    return self.send(400, {"erro": "envie um PDF"})
                up = os.path.join(engine.WORKSPACE, "_uploads")
                os.makedirs(up, exist_ok=True)
                dst = os.path.join(up, name)
                open(dst, "wb").write(self.body())
                with LOCK:
                    m = engine.process(dst)
                ia_run(m["prova_id"], force=True)
                return self.send(200, summary(m))
            data = json.loads(self.body() or b"{}")
            if u.path == "/api/config":
                if data.get("pasta") and not os.path.isdir(data["pasta"]):
                    return self.send(400, {"erro": "pasta não existe"})
                return self.send(200, set_config(**{k: v for k, v in data.items() if k in ("pasta",)}))
            if u.path == "/api/pick_folder":
                p = pick_folder(data.get("inicial") or get_config()["pasta"])
                return self.send(200, {"pasta": set_config(pasta=p)["pasta"] if p else None})
            if u.path == "/api/process":
                pdf = os.path.realpath(data["pdf"])
                if not allowed_pdf(pdf):
                    return self.send(400, {"erro": "PDF fora do acervo e da pasta escolhida"})
                with LOCK:
                    m = engine.process(pdf, data.get("json"))
                ia_run(m["prova_id"], force=True)
                return self.send(200, summary(m))
            pid = data.get("prova")
            if u.path == "/api/ia/run":
                return self.send(200, {"ok": ia_run(pid, force=bool(data.get("force")))})
            if u.path == "/api/ia/desfazer":
                with LOCK:
                    m = load(pid)
                    m["ia_dispensadas"] = [i for i in m.get("ia_dispensadas", []) if i not in set(data["ids"])]
                    engine.save_manifest(wdir(pid), m)
                return self.send(200, {"ok": True})
            if u.path == "/api/ia/dispensar":
                with LOCK:
                    m = load(pid)
                    ds = m.setdefault("ia_dispensadas", [])
                    if data["id"] not in ds:
                        ds.append(data["id"])
                    engine.save_manifest(wdir(pid), m)
                return self.send(200, {"ok": True})
            if u.path == "/api/role":  # troca só o tipo (enunciado / texto-base / alternativa X), sem refazer o recorte
                role = data["role"]
                if role not in ("enunciado", "texto_base", "alt") or (role == "alt" and data.get("letter") not in list("abcde")):
                    return self.send(400, {"erro": "tipo inválido"})
                with LOCK:
                    m = load(pid)
                    c = next(c for c in m["crops"] if c["id"] == data["crop_id"])
                    if (c["role"], c.get("letter")) != (role, data.get("letter") if role == "alt" else None):
                        c["role"] = role
                        c["letter"] = data["letter"] if role == "alt" else None
                        if c.get("exportado"):
                            c["exportado"] = False  # o JSON ainda tem o tipo antigo: precisa gravar de novo
                    engine.save_manifest(wdir(pid), m)
                return self.send(200, c)
            if u.path == "/api/status":
                with LOCK:
                    m = load(pid)
                    ids = set(data["ids"])
                    for c in m["crops"]:
                        if c["id"] in ids:
                            c["status"] = data["status"]
                    engine.save_manifest(wdir(pid), m)
                return self.send(200, summary(m))
            if u.path == "/api/recrop":
                with LOCK:
                    m = load(pid)
                    c = engine.recrop(wdir(pid), m, int(data["page"]), data["rect"], data.get("crop_id"),
                                      data.get("question"), data.get("role") or ("" if data.get("crop_id") else "enunciado"),
                                      data.get("letter"))
                    if data.get("origem") == "ia" and "área da IA" not in c["flags"]:
                        c["flags"].append("área da IA")
                        engine.save_manifest(wdir(pid), m)
                return self.send(200, c)
            if u.path == "/api/reveal":
                m = load(pid)
                c = next(c for c in m["crops"] if c["id"] == data["crop_id"])
                f = os.path.join(wdir(pid), c["file"])
                if sys.platform == "win32" and not data.get("only_path"):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(f)])
                return self.send(200, {"path": os.path.normpath(f)})
            if u.path == "/api/export":
                dry = bool(data.get("dry_run"))
                sel = data.get("questions")
                with LOCK:
                    m = load(pid)
                    r = engine.export(wdir(pid), m, BACKUP_ROOT, dry_run=dry, questions=sel, irmas=data.get("irmas"))
                    if dry:
                        r["historico"] = m.get("exportacoes") or []
                        return self.send(200, r)
                    sel = set(sel) if sel is not None else None
                    for c in m["crops"]:
                        if c["status"] == "aprovado" and (sel is None or sel & set(c["questions"])):
                            c["exportado"] = True
                    m.setdefault("exportacoes", []).append({
                        "em": datetime.datetime.now().isoformat(timespec="seconds"),
                        "imagens": r["imagens_copiadas"], "backup": r.get("backup"),
                        "jsons": {j["arquivo"]: [q["n"] for q in j["questoes"]] for j in r["jsons"]}})
                    engine.save_manifest(wdir(pid), m)
                return self.send(200, r)
            return self.send(404, {"erro": "rota não encontrada"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"erro": str(e)})


class Server(ThreadingHTTPServer):
    # no Windows, SO_REUSEADDR deixa dois servidores na mesma porta (e o navegador cai no antigo)
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def make_server(port):
    os.makedirs(engine.WORKSPACE, exist_ok=True)
    return Server(("127.0.0.1", port), H)


def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else int(os.environ.get("PORT") or 8765)
    srv = make_server(port)
    url = f"http://127.0.0.1:{port}"
    print(f"CropperAprovae rodando em {url}  (Ctrl+C para parar)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
