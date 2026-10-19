"""System 4.0 Image — servidor local da tela de revisão (só biblioteca padrão + PyMuPDF).

Uso: python scripts/system4_image/server.py [--port 8765] [--no-browser]
Abre http://127.0.0.1:8765 — importar PDF, revisar recortes em miniatura, recortar manualmente,
exportar aprovados para Documentos/<Banca>/<Ano>/imagens + JSON (com backup).
"""
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
import engine  # noqa: E402

STATIC = os.path.join(HERE, "static")
BACKUP_ROOT = os.path.join(engine.ROOT, "logs", "system4_image", "backup")
SKIP = {".tmp.driveupload", "backup", "legado", "legado_duplicatas", "legado_antigo", "quarentena", "_quarentena",
        "originais_backup", "_quarentena_imagens", "revisao", "md", "imagens", "extracao"}
LOCK = threading.Lock()
_pdf_cache = {"list": None}


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


def load(pid):
    return json.load(open(os.path.join(wdir(pid), "manifest.json"), encoding="utf-8"))


def summary(m):
    cs = m["crops"]
    return {
        "prova_id": m["prova_id"], "pdf": m["pdf"], "json": m.get("json"), "banca": m.get("banca"), "ano": m.get("ano"),
        "recortes": len(cs), "aprovados": sum(c["status"] == "aprovado" for c in cs),
        "rejeitados": sum(c["status"] == "rejeitado" for c in cs), "pendentes": sum(c["status"] == "pendente" for c in cs), "herdados": sum(c["status"] == "herdado" for c in cs),
        "alertas": sum(1 for q in m["questions"].values() if q["warnings"]), "criado_em": m.get("criado_em"),
    }


_engine_mtime = [os.path.getmtime(engine.__file__)]


def reload_engine():
    """Recarrega o motor se o código mudou (evita exportar com versão antiga em memória)."""
    global engine
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
            if u.path == "/api/provas":
                out = []
                if os.path.isdir(engine.WORKSPACE):
                    for pid in sorted(os.listdir(engine.WORKSPACE)):
                        mp = os.path.join(engine.WORKSPACE, pid, "manifest.json")
                        if os.path.exists(mp):
                            out.append(summary(json.load(open(mp, encoding="utf-8"))))
                return self.send(200, out)
            if u.path == "/api/manifest":
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
                return self.send(200, summary(m))
            data = json.loads(self.body() or b"{}")
            if u.path == "/api/process":
                pdf = os.path.realpath(data["pdf"])
                if not pdf.startswith(os.path.realpath(engine.ROOT)) or not os.path.isfile(pdf):
                    return self.send(400, {"erro": "PDF fora do projeto"})
                with LOCK:
                    m = engine.process(pdf, data.get("json"))
                return self.send(200, summary(m))
            pid = data.get("prova")
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
                                      data.get("question"), data.get("role") or "enunciado", data.get("letter"))
                return self.send(200, c)
            if u.path == "/api/reveal":
                m = load(pid)
                c = next(c for c in m["crops"] if c["id"] == data["crop_id"])
                f = os.path.join(wdir(pid), c["file"])
                if sys.platform == "win32" and not data.get("only_path"):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(f)])
                return self.send(200, {"path": os.path.normpath(f)})
            if u.path == "/api/export":
                with LOCK:
                    m = load(pid)
                    r = engine.export(wdir(pid), m, BACKUP_ROOT)
                    for c in m["crops"]:
                        if c["status"] == "aprovado":
                            c["exportado"] = True
                    engine.save_manifest(wdir(pid), m)
                return self.send(200, r)
            return self.send(404, {"erro": "rota não encontrada"})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"erro": str(e)})


def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    os.makedirs(engine.WORKSPACE, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}"
    print(f"System 4.0 Image rodando em {url}  (Ctrl+C para parar)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
