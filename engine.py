"""System 4.0 Image — motor de detecção e recorte de figuras de questões.

Lê o PDF da prova com PyMuPDF (sem OCR, sem API): acha as âncoras das questões ("73 –"),
as figuras (imagens embutidas + desenhos vetoriais), descarta fórmulas inline e carimbos,
associa cada figura à questão dona (ordem de leitura por coluna, textos-base compartilhados,
alternativas em imagem), recorta a 150 DPI sem incluir texto alheio (nem a letra vermelha do
gabarito) e grava um manifest.json para a tela de revisão.

Uso CLI: python scripts/system4_image/engine.py <pdf> [--json <prova_LLM.json>]
"""
import datetime
import difflib
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata

import pymupdf

# pasta do acervo (System 3.0, com Documentos/ e logs/); padrão: dois níveis acima deste arquivo
ROOT = os.environ.get("SYSTEM3_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(ROOT, "Documentos")
WORKSPACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")
DPI = 150
ZOOM = DPI / 72.0

ANCHOR_RE = re.compile(r"^\s*(?:QUEST[ÃA]O\s*)?(\d{1,3})\s*[–—-]\s*\S", re.I)
ANCHOR_ALT_RE = re.compile(r"^\s*QUEST[ÃA]O\s+(\d{1,3})\b", re.I)
LETTER_RE = re.compile(r"^\s*\(?([a-eA-E])\)\s*")
HEADER_FWD_RE = re.compile(
    r"(?:answer|responda|responder|leia|read|observe|analise|considere)[^\n]{0,60}?"
    r"(?:questions?|quest(?:ões|ão|oes|ao))\s+(\d{1,3})(?:\s*(?:,|and|e|a|to|-|–)\s*(\d{1,3}))?", re.I)
HEADER_BACK_RE = re.compile(
    r"(?:quest(?:ões|oes)|questions)\s+(?:de\s+)?(\d{1,3})\s*(?:a|e|and|to|-|–)\s*(\d{1,3})"
    r"[^\n]{0,40}(?:acima|above)", re.I)
FIG_WORD_RE = re.compile(r"\b(?:figura|gr[aá]fico|imagem|tabela|mapa|charge|tirinha|quadrinho|esquema|circuito|diagrama|"
                         r"comic strip|cartoon|picture|figure)\b", re.I)


# ----------------------------------------------------------------------------- utilidades
def norm_tokens(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]{3,}", s)


def is_red(color):
    if color is None:
        return False
    if isinstance(color, int):
        r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    else:
        r, g, b = [int(255 * c) for c in color[:3]]
    return r > 170 and g < 90 and b < 90


def rect_gap(a, b):
    dx = max(0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return max(dx, dy)


def prova_id_from_pdf(pdf_path):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)


def find_json_for_pdf(pdf_path):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    folder = os.path.join(os.path.dirname(pdf_path), "extracao")
    cands = sorted(glob.glob(os.path.join(folder, f"{glob.escape(stem)}*_LLM.json")) +
                   glob.glob(os.path.join(folder, f"*{glob.escape(stem)}*_LLM.json")))
    cands = [c for c in dict.fromkeys(cands)]
    return cands[0] if cands else None


# ----------------------------------------------------------------------------- leitura da página
class Page:
    def __init__(self, pg, index):
        self.pg = pg
        self.index = index
        self.rect = pg.rect
        self.mid = pg.rect.width / 2
        self.lines = []  # dict(text, rect, col, red)
        d = pg.get_text("dict")
        for b in d["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                text = "".join(s["text"] for s in ln["spans"])
                if not text.strip():
                    continue
                r = pymupdf.Rect(ln["bbox"])
                red = any(is_red(s.get("color")) and s["text"].strip() for s in ln["spans"])
                self.lines.append({"text": text, "rect": r, "col": self.col_of(r), "red": red,
                                   "spans": [(pymupdf.Rect(s["bbox"]), s["text"], is_red(s.get("color"))) for s in ln["spans"]]})
        self.lines.sort(key=lambda l: (l["col"], l["rect"].y0, l["rect"].x0))
        self.rules = []
        self.small_drawings = []
        for dr in pg.get_drawings():
            r = pymupdf.Rect(dr["rect"])
            if r.width < 60 and r.height < 60 and (r.width > 1 or r.height > 1):
                self.small_drawings.append(r)
            if (r.width > 180 and r.height < 2.5) or (r.height > 180 and r.width < 2.5):
                self.rules.append(r)
            elif r.width > 150 and r.height > 150:  # moldura: as 4 bordas viram réguas
                self.rules += [pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + 1), pymupdf.Rect(r.x0, r.y1 - 1, r.x1, r.y1),
                               pymupdf.Rect(r.x0, r.y0, r.x0 + 1, r.y1), pymupdf.Rect(r.x1 - 1, r.y0, r.x1, r.y1)]
        # limites de coluna pelo texto
        self.col_bounds = {}
        for c in (0, 1):
            # só linhas que cabem inteiras na coluna (rodapé centralizado/títulos largos distorcem a margem)
            xs = [l["rect"] for l in self.lines if l["col"] == c and
                  (l["rect"].x1 <= self.mid + 5 if c == 0 else l["rect"].x0 >= self.mid - 5)]
            if xs:
                x0s = sorted(r.x0 for r in xs)
                self.col_bounds[c] = (x0s[len(x0s) // 20] - 4, max(r.x1 for r in xs) + 4)
        self.col_bounds.setdefault(0, (self.rect.x0 + 10, self.mid - 2))
        self.col_bounds.setdefault(1, (self.mid + 2, self.rect.x1 - 10))
        if self.col_bounds[0][1] > self.mid + 10:  # página de coluna única
            self.col_bounds[0] = (self.col_bounds[0][0], self.mid - 2)

    def col_of(self, r):
        return 0 if (r.x0 + r.x1) / 2 < self.mid else 1

    def col_rect(self, col):
        if col == 0:
            return pymupdf.Rect(self.rect.x0 + 3, self.rect.y0 + 4, self.mid - 4, self.rect.y1 - 4)
        return pymupdf.Rect(self.mid + 4, self.rect.y0 + 4, self.rect.x1 - 3, self.rect.y1 - 4)


def find_anchors(pages, expected):
    """Âncoras (número, página, coluna, y). Mantém só sequência crescente coerente."""
    raw = []
    for p in pages:
        for l in p.lines:
            m = ANCHOR_RE.match(l["text"]) or ANCHOR_ALT_RE.match(l["text"])
            if not m:
                continue
            n = int(m.group(1))
            cx0 = p.col_bounds[l["col"]][0]
            if l["rect"].x0 - cx0 > 18:  # âncora fica colada na margem da coluna
                continue
            if expected and n not in expected:
                continue
            raw.append({"n": n, "page": p.index, "col": l["col"], "y": l["rect"].y0, "x": l["rect"].x0,
                        "rect": l["rect"]})
    raw.sort(key=lambda a: (a["page"], a["col"], a["y"]))
    out, last = [], 0
    for a in raw:
        if a["n"] > last and (not out or a["n"] - last <= 6):
            out.append(a)
            last = a["n"]
    return out


def find_headers(pages):
    hs = []
    for p in pages:
        for l in p.lines:
            t = l["text"]
            m = HEADER_BACK_RE.search(t)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                hs.append({"page": p.index, "col": l["col"], "y": l["rect"].y0, "dir": "back",
                           "nums": list(range(min(a, b), max(a, b) + 1))})
                continue
            m = HEADER_FWD_RE.search(t)
            if m:
                a = int(m.group(1))
                b = int(m.group(2)) if m.group(2) else a
                sep = t[m.start(2) - 4:m.start(2)] if m.group(2) else ""
                nums = list(range(a, b + 1)) if re.search(r"\b(a|to)\b|[-–]", sep) else sorted({a, b})
                hs.append({"page": p.index, "col": l["col"], "y": l["rect"].y0, "dir": "fwd", "nums": nums})
    return hs


# ----------------------------------------------------------------------------- figuras
def figure_candidates(p):
    pg = p.pg
    W, H = p.rect.width, p.rect.height
    items, ignored = [], []
    for im in pg.get_image_info():
        r = pymupdf.Rect(im["bbox"]) & p.rect
        if r.is_empty or r.width < 6 or r.height < 6:
            continue
        if r.width * r.height > 0.8 * W * H:
            ignored.append({"bbox": list(r), "reason": "imagem de página inteira (PDF escaneado?)"})
            continue
        items.append({"rect": r, "src": "img"})
    drects = []
    for d in pg.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if r.width > 0.8 * W or r.height > 0.8 * H:
            continue  # moldura, divisória de coluna
        if r.width < 1.5 and r.height < 1.5:
            continue
        red = is_red(d.get("color")) or is_red(d.get("fill"))
        if red:
            continue  # carimbo ANULADA / marcação do gabarito
        drects.append((r, red))
    # agrupa desenhos próximos
    clusters = []
    for r, red in drects:
        placed = False
        for c in clusters:
            if rect_gap(c["rect"], r) <= 6:
                c["rect"] |= r
                c["n"] += 1
                c["red"] += red
                placed = True
                break
        if not placed:
            clusters.append({"rect": pymupdf.Rect(r), "n": 1, "red": int(red)})
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if rect_gap(clusters[i]["rect"], clusters[j]["rect"]) <= 6:
                    clusters[i]["rect"] |= clusters[j]["rect"]
                    clusters[i]["n"] += clusters[j]["n"]
                    clusters[i]["red"] += clusters[j]["red"]
                    clusters.pop(j)
                    changed = True
                    break
            if changed:
                break
    for c in clusters:
        r = c["rect"]
        if c["red"] > c["n"] * 0.5:
            ignored.append({"bbox": list(r), "reason": "desenho vermelho (carimbo ANULADA / marcação de gabarito)"})
            continue
        if r.width < 40 or r.height < 30:
            continue  # traço de fração, sublinhado, setinha de vetor
        # moldura em volta de texto (caixa de enunciado) — só borda, texto dentro
        inside = [l for l in p.lines if l["rect"].intersects(r)]
        if c["n"] <= 4 and len(inside) >= 3:
            continue
        items.append({"rect": r, "src": "vetor"})
    # junta peças sobrepostas/encostadas (imagem fatiada, vetor + imagem)
    def has_label(r):
        for l in p.lines:
            lr = l["rect"]
            if LETTER_RE.match(l["text"]) and lr.x0 < r.x0 + 4 and r.x0 - lr.x0 < 60 and r.y0 - 8 <= lr.y0 <= r.y0 + 15:
                return True
        return False

    merged = []
    for it in sorted(items, key=lambda i: (i["rect"].y0, i["rect"].x0)):
        for m in merged:
            if p.col_of(m["rect"]) == p.col_of(it["rect"]) and rect_gap(m["rect"], it["rect"]) <= 4                     and not (has_label(it["rect"]) and it["rect"].y0 > m["rect"].y0 + 6):
                m["rect"] |= it["rect"]
                m["pieces"] += 1
                m["srcs"].add(it["src"])
                break
        else:
            merged.append({"rect": pymupdf.Rect(it["rect"]), "pieces": 1, "srcs": {it["src"]}})
    return merged, ignored


def same_band(a, b, frac=0.5):
    ov = min(a.y1, b.y1) - max(a.y0, b.y0)
    return ov > frac * min(a.height, b.height)


def classify(p, fig, has_latex=False):
    """'formula' (imagem inline no meio do texto), 'alt' (alternativa em imagem) ou 'figura'."""
    r = fig["rect"]
    col = p.col_of(r)
    for l in p.lines:
        if l["col"] != col:
            continue
        m = LETTER_RE.match(l["text"])
        if not m:
            continue
        lr = l["rect"]
        label_r = pymupdf.Rect(lr.x0, lr.y0, lr.x0 + 6, lr.y1)
        acc = ""
        for sr, st, _ in l["spans"]:
            acc += st
            label_r |= pymupdf.Rect(sr.x0, sr.y0, min(sr.x1, sr.x0 + 6 + 5 * len(st)), sr.y1)
            if ")" in acc and len(st.strip()) <= 3:
                label_r.x1 = sr.x1
                break
            if ")" in acc:
                cut = sr.x0 + (sr.x1 - sr.x0) * (st.index(")") + 1) / max(1, len(st)) if ")" in st else sr.x1
                label_r.x1 = min(label_r.x1, cut)
                break
        near_top = r.y0 - 8 <= lr.y0 <= r.y0 + max(10, min(0.5 * r.height, 60))
        if label_r.x1 <= r.x0 + 8 and r.x0 - label_r.x0 < 60 and near_top:
            return "alt", m.group(1).lower(), label_r
    # fórmula inline: há texto corrido na mesma faixa, colado à esquerda ou à direita da imagem
    if r.height <= 45 and r.width <= 260:
        return "formula", None, None
    if r.height < 110 and r.width / max(1, r.height) >= 1.2:
        for l in p.lines:
            if l["col"] != col:
                continue
            for sr, st, _ in l["spans"]:
                if not re.search(r"[A-Za-zÀ-ú0-9]", st) or (sr & r).get_area() > 0.5 * sr.get_area():
                    continue
                if same_band(sr, r, 0.15) and r.y0 - 2 <= (sr.y0 + sr.y1) / 2 <= r.y1 + 2 and                         (0 <= r.x0 - sr.x1 <= 25 or 0 <= sr.x0 - r.x1 <= 25):
                    return "formula", None, None
    if has_latex and r.height < 40:
        return "formula", None, None
    return "figura", None, None


def crop_rect(p, fig_rect, label_rect=None, margin_frac=0.20, others=()):
    """Margem de respiro, encolhida para não engolir texto alheio (enunciado, alternativas, gabarito)."""
    r0 = pymupdf.Rect(fig_rect)
    col = p.col_of(r0)
    for l in p.lines:
        lr = l["rect"]
        paragraph = lr.x0 - p.col_bounds[col][0] < 8 or LETTER_RE.match(l["text"]) or l["red"]
        if l["col"] != col or not lr.intersects(r0) or (len(l["text"].strip()) < 25 and not paragraph):
            continue
        inter = lr & r0
        if inter.get_area() > 0.85 * lr.get_area() and r0.y0 + 15 < lr.y0 and lr.y1 < r0.y1 - 15:
            continue  # texto realmente dentro da figura
        cy = (lr.y0 + lr.y1) / 2
        if cy < (r0.y0 + r0.y1) / 2:
            ny0 = lr.y1 + 1
            if r0.y1 - ny0 > 0.5 * fig_rect.height:
                r0.y0 = ny0
        else:
            ny1 = lr.y0 - 1
            if ny1 - r0.y0 > 0.5 * fig_rect.height:
                r0.y1 = ny1
    fig_core = pymupdf.Rect(r0)
    # rótulos curtos colados à figura (eixos, "F (N)", "t (s)") entram na figura
    for l in p.lines:
        lr = l["rect"]
        t = l["text"].strip()
        if l["col"] != col or lr.intersects(r0):
            continue
        if lr.x0 - p.col_bounds[col][0] < 8 and lr.x1 < r0.x0 - 2 or (len(t) > 3 and lr.x0 - p.col_bounds[col][0] < 8 and not lr.intersects(fig_rect)):
            continue  # texto de parágrafo, não é rótulo da figura
        if len(t) <= 14 and re.search(r"[A-Za-zÀ-ú0-9]", t) and rect_gap(lr, r0) <= 10 and not LETTER_RE.match(t)                 and not ANCHOR_RE.match(t) and not l["red"]:
            r0 |= lr
    m = max(8.0, margin_frac * min(max(r0.width, r0.height), 160))
    R = pymupdf.Rect(r0.x0 - m, r0.y0 - m, r0.x1 + m, r0.y1 + m) & p.col_rect(col)
    obst = [l["rect"] for l in p.lines] + p.rules + [o for o in others if not o.intersects(fig_rect)] +         [d for d in p.small_drawings if not d.intersects(fig_rect)]
    for lr in obst:
        if lr.intersects(r0) or not lr.intersects(R):
            continue
        if lr.y1 <= r0.y0 + 2:
            R.y0 = max(R.y0, lr.y1 + 1)
        elif lr.y0 >= r0.y1 - 2:
            R.y1 = min(R.y1, lr.y0 - 1)
        elif lr.x1 <= r0.x0 + 2:
            R.x0 = max(R.x0, lr.x1 + 1)
        elif lr.x0 >= r0.x1 - 2:
            R.x1 = min(R.x1, lr.x0 - 1)
    if label_rect is not None:
        R.x0 = max(R.x0, min(label_rect.x1 + 1, fig_core.x0))
    R |= fig_core & p.col_rect(col)  # nunca cortar a figura em si
    if label_rect is not None:
        R.x0 = max(R.x0, min(label_rect.x1 + 1, fig_core.x0))
    return R


def red_inside(p, R, fig_rect):
    for l in p.lines:
        for sr, st, red in l["spans"]:
            if red and st.strip() and (sr & R).get_area() > 0.3 * max(1e-6, sr.get_area()):
                return True
    return False


# ----------------------------------------------------------------------------- texto da questão (checagem JSON x PDF)
def question_text(pages, anchors):
    order = [(a["page"], a["col"], a["y"]) for a in anchors]
    out = {a["n"]: [] for a in anchors}
    for p in pages:
        for l in p.lines:
            key = (p.index, l["col"], l["rect"].y0)
            owner = None
            for i, k in enumerate(order):
                if k <= key:
                    owner = anchors[i]["n"]
                else:
                    break
            if owner is not None:
                out[owner].append(l["text"])
    return {n: " ".join(v) for n, v in out.items()}


def text_check(pdf_text, q):
    jt = norm_tokens((q.get("statement_cleaned") or "") + " " +
                     " ".join(str(a.get("text")) for a in q.get("alternatives") or [] if isinstance(a, dict)))
    pt = set(norm_tokens(pdf_text))
    if not jt:
        return None
    jt = [t for t in jt if not t.isdigit() or len(t) > 3]
    if not jt:
        return None
    return round(sum(1 for t in jt if t in pt) / len(jt), 2)


# ----------------------------------------------------------------------------- pipeline
def render(pg, rect, path):
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), clip=rect, alpha=False)
    pix.save(path)
    return pix.width, pix.height


def process(pdf_path, json_path=None, workspace=WORKSPACE, log=print):
    pdf_path = os.path.abspath(pdf_path)
    pid = prova_id_from_pdf(pdf_path)
    if json_path is None:
        json_path = find_json_for_pdf(pdf_path)
    wdir = os.path.join(workspace, pid)
    os.makedirs(os.path.join(wdir, "crops"), exist_ok=True)
    os.makedirs(os.path.join(wdir, "pages"), exist_ok=True)
    qjson = {}
    meta = {}
    if json_path and os.path.exists(json_path):
        d = json.load(open(json_path, encoding="utf-8"))
        qs = d.get("questions") if isinstance(d, dict) else d
        for q in qs or []:
            if isinstance(q.get("source_number"), int):
                qjson[q["source_number"]] = q
        meta = {k: d.get(k) for k in ("banca", "ano", "prova")} if isinstance(d, dict) else {}
    doc = pymupdf.open(pdf_path)
    pages = [Page(doc[i], i) for i in range(len(doc))]
    anchors = find_anchors(pages, set(qjson) if qjson else None)
    headers = find_headers(pages)
    log(f"[{pid}] {len(doc)} páginas, {len(anchors)} âncoras de questão, {len(headers)} cabeçalhos de texto-base")
    order = [(a["page"], a["col"], a["y"]) for a in anchors]

    def owner_of(page, col, y):
        idx = None
        for i, k in enumerate(order):
            if k <= (page, col, y):
                idx = i
            else:
                break
        return idx

    pages_meta = []
    for p in pages:
        fn = f"p{p.index + 1:02d}.png"
        w, h = render(p.pg, p.rect, os.path.join(wdir, "pages", fn))
        pages_meta.append({"n": p.index + 1, "file": f"pages/{fn}", "pdf_w": p.rect.width, "pdf_h": p.rect.height,
                           "px_w": w, "px_h": h})
    crops, ignored = [], []
    first_anchor_page = anchors[0]["page"] if anchors else 0
    for p in pages:
        if p.index < first_anchor_page:
            continue
        figs, ign = figure_candidates(p)
        for g in ign:
            g["page"] = p.index + 1
            ignored.append(g)
        for f in figs:
            r = f["rect"]
            col = p.col_of(r)
            ykey = r.y0 + min(r.height / 2, 20)
            idx0 = owner_of(p.index, col, ykey)
            qj = qjson.get(anchors[idx0]["n"], {}) if idx0 is not None else {}
            kind, letter, label = classify(p, f, bool(qj.get("has_latex")))
            if kind == "alt" and qj:
                at = next((str(a.get("text") or "") for a in qj.get("alternatives") or []
                           if str(a.get("letter", "")).lower() == letter), "")
                if re.search(r"\$|\\(?:frac|sqrt|operatorname|hat|vec)", at):
                    ignored.append({"page": p.index + 1, "bbox": [round(x, 1) for x in r],
                                    "reason": f"alternativa {letter.upper()} em fórmula já transcrita em LaTeX no JSON"})
                    continue
            if kind == "formula":
                ignored.append({"page": p.index + 1, "bbox": [round(x, 1) for x in r], "reason": "fórmula inline (texto já está no JSON)"})
                continue
            idx = idx0
            flags = []
            conf = 0.92
            owners = []
            role = "alt" if kind == "alt" else "enunciado"
            # texto-base compartilhado
            hdr = None
            for h in headers:
                hk = (h["page"], h["col"], h["y"])
                fk = (p.index, col, ykey)
                prev_k = order[idx] if idx is not None else (-1, -1, -1)
                next_k = order[idx + 1] if idx is not None and idx + 1 < len(order) else (9999, 9, 9e9)
                if h["dir"] == "fwd" and prev_k <= hk <= fk < next_k:
                    hdr = h
                if h["dir"] == "back" and prev_k <= fk <= hk < next_k:
                    hdr = h
            if hdr and role != "alt":
                owners = [n for n in hdr["nums"] if not qjson or n in qjson]
                role = "texto_base"
                flags.append(f"texto-base das questões {', '.join(map(str, owners))}")
            elif idx is not None:
                owners = [anchors[idx]["n"]]
                a = anchors[idx]
                if (a["page"], a["col"]) != (p.index, col):
                    conf -= 0.2
                    flags.append("continuação de outra coluna/página")
            else:
                conf -= 0.5
                flags.append("antes da primeira questão")
            if not owners:
                continue
            if "vetor" in f["srcs"] and "img" not in f["srcs"]:
                conf -= 0.12
                flags.append("desenho vetorial")
            if f["pieces"] > 3:
                conf -= 0.08
                flags.append(f"{f['pieces']} peças unidas")
            R = crop_rect(p, r, label, others=[g["rect"] for g in figs if g is not f])
            if red_inside(p, R, r):
                conf -= 0.35
                flags.append("texto vermelho no recorte (gabarito vazando?)")
            if R.width < r.width - 1 or R.height < r.height - 1:
                flags.append("figura encostada em texto")
            q0 = owners[0]
            suffix = f"_alt{letter.upper()}" if role == "alt" else ("_txt" if role == "texto_base" else "")
            base = f"{pid}_q{q0}_p{p.index + 1}{suffix}"
            k = 1
            name = base + ".png"
            while any(c["file"].endswith("/" + name) for c in crops):
                k += 1
                name = f"{base}_{k}.png"
            render(p.pg, R, os.path.join(wdir, "crops", name))
            crops.append({
                "id": hashlib.md5(f"{pid}|{p.index}|{r}".encode()).hexdigest()[:10],
                "questions": owners, "role": role, "letter": letter, "page": p.index + 1,
                "bbox_pdf": [round(x, 2) for x in r], "rect_pdf": [round(x, 2) for x in R],
                "file": f"crops/{name}", "confidence": round(max(0.05, conf), 2),
                "flags": flags, "status": "pendente", "origem": "auto", "v": 1,
            })
    # alternativas: se 2+ letras da mesma questão viraram imagem, as demais provavelmente também
    by_q = {}
    for c in crops:
        for n in c["questions"]:
            by_q.setdefault(n, []).append(c)
    ptext = question_text(pages, anchors)
    questions = {}
    anchor_by_n = {a["n"]: a for a in anchors}
    nums = sorted(set(qjson) | set(anchor_by_n))
    for n in nums:
        q = qjson.get(n, {})
        cs = by_q.get(n, [])
        st = q.get("statement_cleaned") or ""
        alts = [{"letter": a.get("letter"), "text": a.get("text")} for a in q.get("alternatives") or [] if isinstance(a, dict)]
        cita = bool(FIG_WORD_RE.search(st))
        warn = []
        if n not in anchor_by_n:
            warn.append("âncora não encontrada no PDF")
        if cita and not cs:
            warn.append("enunciado cita figura, mas nada foi detectado")
        alt_letters = sorted({c["letter"] for c in cs if c["role"] == "alt"})
        if alt_letters and alts and all(norm_tokens(a["text"]) for a in alts) and                 not any("$" in str(a["text"]) for a in alts):
            warn.append("alternativas são imagem no PDF, mas o JSON tem texto — confira se não foi inventado na extração")
        if alt_letters and alts and len(alt_letters) < len(alts):
            warn.append(f"só {len(alt_letters)} de {len(alts)} alternativas em imagem")
        tc = text_check(ptext.get(n, ""), q) if q else None
        if tc is not None and tc < 0.6:
            warn.append(f"texto do JSON diverge do PDF ({int(tc * 100)}% das palavras batem) — confira (fórmula em imagem também derruba)")
        questions[str(n)] = {
            "n": n, "page": anchor_by_n[n]["page"] + 1 if n in anchor_by_n else None,
            "statement": st[:700], "alternatives": alts, "status_json": q.get("status"),
            "gabarito": q.get("gabarito"), "cita_figura": cita, "texto_check": tc, "warnings": warn,
            "pdf_text": ptext.get(n, "")[:900],
        }
    manifest = {
        "prova_id": pid, "pdf": pdf_path, "json": json_path, "banca": meta.get("banca"), "ano": meta.get("ano"),
        "criado_em": datetime.datetime.now().isoformat(timespec="seconds"), "dpi": DPI,
        "pages": pages_meta, "questions": questions, "crops": crops, "ignored": ignored,
    }
    old = os.path.join(wdir, "manifest.json")
    if os.path.exists(old):  # preserva decisões já tomadas
        prev = json.load(open(old, encoding="utf-8"))
        dec = {c["id"]: c for c in prev.get("crops", [])}
        for i, c in enumerate(manifest["crops"]):
            old_c = dec.get(c["id"])
            if not old_c:
                continue
            if "recorte manual" in old_c.get("flags", []):  # ajuste manual vence a detecção automática
                manifest["crops"][i] = old_c
                render(doc[old_c["page"] - 1], pymupdf.Rect(old_c["rect_pdf"]), os.path.join(wdir, old_c["file"]))
            elif old_c["status"] != "pendente":
                c["status"] = old_c["status"]
        manifest["crops"] += [c for c in prev.get("crops", []) if c.get("origem") == "manual"]
    # mesma questão (mesmo id) já está na área de trabalho de uma prova irmã processada antes: revise lá
    sib_ids = set()
    if json_path:
        for sib in glob.glob(os.path.join(os.path.dirname(json_path), "*_LLM.json")):
            if os.path.samefile(sib, json_path):
                continue
            spid = prova_id_from_pdf(re.sub(r"_COMENTADA_LLM\.json$|_LLM\.json$", ".pdf", os.path.basename(sib)))
            smf = os.path.join(workspace, spid, "manifest.json")
            if not os.path.exists(smf):
                continue
            try:
                sd = json.load(open(sib, encoding="utf-8"))
                sm = json.load(open(smf, encoding="utf-8"))
            except Exception:
                continue
            s_num2id = {q.get("source_number"): q.get("id") for q in sd.get("questions") or []}
            for c in sm.get("crops", []):
                for n_ in c["questions"]:
                    sib_ids.add((s_num2id.get(n_), spid))
    sib_map = {}
    for i_, sp in sib_ids:
        sib_map.setdefault(i_, sp)
    for c in manifest["crops"]:
        if c["status"] != "pendente":
            continue
        ids_ = [qjson.get(n, {}).get("id") for n in c["questions"]]
        if ids_ and all(i in sib_map for i in ids_):
            c["status"] = "herdado"
            c["flags"].append(f"mesma questão da {sib_map[ids_[0]]} — revise lá; a exportação de lá preenche esta prova")
    # questão que já tem imagem no JSON (exportada antes ou herdada da prova irmã OP1/OP2): não precisa revisar
    herd = 0
    for c in manifest["crops"]:
        if c["status"] != "pendente":
            continue
        qs_ = [qjson.get(n) for n in c["questions"]]
        if qs_ and all(q and q.get("imagens") and all(
                os.path.isfile(os.path.normpath(os.path.join(os.path.dirname(json_path), i))) for i in q["imagens"])
                for q in qs_):
            c["status"] = "herdado"
            c["flags"].append("questão já tem imagem no JSON (exportada antes ou vinda da prova irmã)")
            herd += 1
    save_manifest(wdir, manifest)
    log(f"[{pid}] {herd} recortes herdados (questão já tem imagem)")
    log(f"[{pid}] {len(crops)} recortes, {len(ignored)} detecções ignoradas, "
        f"{sum(1 for q in questions.values() if q['warnings'])} questões com alerta")
    return manifest


def save_manifest(wdir, manifest):
    tmp = os.path.join(wdir, "manifest.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(wdir, "manifest.json"))


def recrop(wdir, manifest, page_n, rect_pdf, crop_id=None, question=None, role="enunciado", letter=None):
    doc = pymupdf.open(manifest["pdf"])
    pg = doc[page_n - 1]
    R = pymupdf.Rect(rect_pdf) & pg.rect
    pid = manifest["prova_id"]
    if crop_id:
        c = next(c for c in manifest["crops"] if c["id"] == crop_id)
        c["v"] = c.get("v", 1) + 1
        c["page"] = page_n
    else:
        q = int(question)
        suffix = f"_alt{letter.upper()}" if role == "alt" and letter else ""
        base = f"{pid}_q{q}_p{page_n}{suffix}"
        name, k = base + ".png", 1
        while any(x["file"].endswith("/" + name) for x in manifest["crops"]):
            k += 1
            name = f"{base}_{k}.png"
        c = {"id": hashlib.md5(f"{pid}|manual|{q}|{R}|{datetime.datetime.now()}".encode()).hexdigest()[:10],
             "questions": [q], "role": role, "letter": letter, "page": page_n, "bbox_pdf": None,
             "file": f"crops/{name}", "confidence": 1.0, "flags": ["recorte manual"], "origem": "manual", "v": 1}
        manifest["crops"].append(c)
    c["rect_pdf"] = [round(x, 2) for x in R]
    c["status"] = "aprovado"
    if "recorte manual" not in c["flags"]:
        c["flags"].append("recorte manual")
    render(pg, R, os.path.join(wdir, c["file"]))
    save_manifest(wdir, manifest)
    return c


# ----------------------------------------------------------------------------- exportação para Documentos
def _alt_name(qid, letter):
    """Nome da imagem de alternativa: igual em todas as provas irmãs (o importador do Aprovae compara o
    nome do arquivo no hash de conteúdo) e terminado em _A.png (é assim que ele reconhece a letra)."""
    return f"alt_{str(qid).replace('-', '')[:12]}_{letter.upper()}.png"


def _backup_json(jp, backup_root, stamp):
    import shutil
    rel = os.path.relpath(jp, DOCS)
    if rel.startswith(".."):
        rel = os.path.basename(jp)
    bk = os.path.join(backup_root, stamp, rel)
    os.makedirs(os.path.dirname(bk), exist_ok=True)
    if not os.path.exists(bk):
        shutil.copy2(jp, bk)
    return bk


def _save_json(jp, d):
    tmp = jp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    json.load(open(tmp, encoding="utf-8"))
    os.replace(tmp, jp)


def _apply(d, jp, pid, plan, shared, log):
    """Grava no JSON `d` as imagens do plano {qid: {...}} e os grupos compartilhados."""
    by_id = {q.get("id"): q for q in d["questions"]}
    n = 0
    for qid, item in plan.items():
        q = by_id.get(qid)
        if not q:
            continue
        old = q.get("imagens") or []
        # tira referências antigas geradas pelo System 4 para esta questão (reexportação idempotente)
        mine = re.compile(rf"^\.\./imagens/(?:{re.escape(pid)}_q{item['n']}_|alt_{str(qid).replace('-', '')[:12]}_)")
        keep = [i for i in old if not mine.match(i) and i not in item["stmt"] and i not in item["alts"].values()]
        q["imagens"] = list(dict.fromkeys(keep + item["stmt"] + [item["alts"][L] for L in sorted(item["alts"])]))
        q["has_image"] = True
        for a in q.get("alternatives") or []:
            L = str(a.get("letter", "")).upper()
            if L in item["alts"]:
                if a.get("text") != f"img{L}":
                    log.append((os.path.basename(jp), q.get("source_number"), f"alternativa {L} vira imagem", a.get("text")))
                a["text"] = f"img{L}"
        n += 1
    if shared:
        ia = d.setdefault("imagens_associadas", [])
        for nums_ids, url in shared:
            iid = "img_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            linked = [q for q in d["questions"] if q.get("id") in nums_ids]
            if not linked:
                continue
            entry = next((x for x in ia if x.get("id") == iid), None)
            if entry is None:
                entry = {"id": iid, "title": f"Figura do texto-base das questões {linked[0]['source_number']} a "
                                             f"{linked[-1]['source_number']}", "url": url, "imagens": [url],
                         "question_ids": [], "source_numbers": []}
                ia.append(entry)
            entry["imagens"] = [url]
            for q in linked:
                q["imagem_associada_id"] = iid
                q["imagens"] = list(dict.fromkeys((q.get("imagens") or []) + [url]))
                q["has_image"] = True
            # vínculo bidirecional na ordem das questões
            allq = [q for q in d["questions"] if q.get("imagem_associada_id") == iid]
            entry["question_ids"] = [q["id"] for q in allq]
            entry["source_numbers"] = [q["source_number"] for q in allq]
            n += len(linked)
    return n


def export(wdir, manifest, backup_root):
    """Copia os recortes aprovados para <pasta da prova>/imagens e grava no JSON e nas provas irmãs
    (mesmo id em outro JSON da mesma pasta extracao/, ex.: CFS OP1 -> OP2). Backup + escrita atômica."""
    import shutil
    pdf_dir = os.path.dirname(manifest["pdf"])
    img_dir = os.path.join(pdf_dir, "imagens")
    os.makedirs(img_dir, exist_ok=True)
    pid = manifest["prova_id"]
    aprov = [c for c in manifest["crops"] if c["status"] == "aprovado"]
    result = {"imagens_copiadas": 0, "pasta": img_dir, "json_atualizado": False, "questoes": 0, "irmas": {}, "log": []}
    jp = manifest.get("json")
    d = json.load(open(jp, encoding="utf-8")) if jp and os.path.exists(jp) else None
    num2id = {q.get("source_number"): q.get("id") for q in d["questions"]} if d else {}
    plan, shared = {}, []
    pages_used = set()
    for c in aprov:
        src = os.path.join(wdir, c["file"])
        pages_used.add(c["page"])
        if c["role"] == "alt" and c.get("letter") and d:
            n = c["questions"][0]
            qid = num2id.get(n)
            name = _alt_name(qid, c["letter"])
        else:
            name = os.path.basename(c["file"])
        shutil.copy2(src, os.path.join(img_dir, name))
        result["imagens_copiadas"] += 1
        url = "../imagens/" + name
        if not d:
            continue
        if c["role"] == "texto_base" and len(c["questions"]) > 1:
            shared.append(({num2id[n] for n in c["questions"] if n in num2id}, url))
            continue
        for n in c["questions"]:
            qid = num2id.get(n)
            if not qid:
                continue
            it = plan.setdefault(qid, {"n": n, "stmt": [], "alts": {}})
            if c["role"] == "alt" and c.get("letter"):
                it["alts"][c["letter"].upper()] = url
            else:
                it["stmt"].append(url)
    for pn in sorted(pages_used):  # fallback página inteira (manual §8.2)
        shutil.copy2(os.path.join(wdir, "pages", f"p{pn:02d}.png"), os.path.join(img_dir, f"{pid}_p{pn}_full.png"))
        result["imagens_copiadas"] += 1
    if not d:
        return result
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    result["backup"] = _backup_json(jp, backup_root, stamp)
    result["questoes"] = _apply(d, jp, pid, plan, shared, result["log"])
    _save_json(jp, d)
    result["json_atualizado"] = True
    # provas irmãs: mesma pasta extracao/, mesmo id de questão
    ids = set(plan) | {i for s, _ in shared for i in s}
    for sib in sorted(glob.glob(os.path.join(os.path.dirname(jp), "*_LLM.json"))):
        if os.path.samefile(sib, jp):
            continue
        try:
            sd = json.load(open(sib, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(sd, dict) or not isinstance(sd.get("questions"), list):
            continue
        if not ids & {q.get("id") for q in sd["questions"]}:
            continue
        _backup_json(sib, backup_root, stamp)
        sib_plan = {}
        for qid, it in plan.items():
            sq = next((q for q in sd["questions"] if q.get("id") == qid), None)
            if sq:
                sib_plan[qid] = {"n": sq["source_number"], "stmt": it["stmt"], "alts": it["alts"]}
        k = _apply(sd, sib, pid, sib_plan, shared, result["log"])
        _save_json(sib, sd)
        result["irmas"][os.path.basename(sib)] = k
    return result


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    jp = None
    if "--json" in args:
        jp = args[args.index("--json") + 1]
    process(args[0], jp)
