"""Protótipo: modelo de layout (IA) como segunda opinião para as figuras do engine.

Roda um modelo de layout numa página do PDF e devolve as caixas de figura em coordenadas
do PDF (pontos), no mesmo espaço de `engine.figure_candidates` / `bbox_pdf` do manifest.

Dois backends, ambos só CPU e sem OCR:
  - "surya":   layout do Surya (o mesmo que o Marker usa), pacote `surya-ocr`.
  - "docling": modelo de layout do Docling (RT-DETR "heron"), via `docling_ibm_models`, sem o pipeline.

Não faz parte do engine nem do export: roda no venv separado `.venv-ai`
(python -m venv .venv-ai; pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu;
pip install pymupdf docling openai platformdirs; pip install --no-deps surya-ocr==0.22.1 — o surya fixa pillow<11,
que não tem wheel para Python 3.14). Resultado da comparação: docs/avaliacao_hibrido.md.

Uso CLI: .venv-ai/Scripts/python layout_ai.py <pdf> <página 1-based> [--backend surya|docling]
"""
import argparse
import json
import os
import time

import pymupdf
from PIL import Image

DPI = 150  # mesma resolução do engine; os dois modelos redimensionam por dentro
FIG_LABELS = {"picture", "figure", "image", "chart", "diagram", "chemicalblock"}


def render_page(pg, dpi=DPI):
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _to_pdf(box_px, img, pg):
    """px da imagem renderizada -> pontos do PDF (origem no canto superior esquerdo, como o PyMuPDF)."""
    sx, sy = pg.rect.width / img.width, pg.rect.height / img.height
    x0, y0, x1, y1 = box_px
    return [round(pg.rect.x0 + x0 * sx, 2), round(pg.rect.y0 + y0 * sy, 2),
            round(pg.rect.x0 + x1 * sx, 2), round(pg.rect.y0 + y1 * sy, 2)]


# ----------------------------------------------------------------------------- backends
class SuryaLayout:
    name = "surya"

    def __init__(self):
        # surya >= 0.22: o LayoutPredictor "cheio" é um VLM que exige servidor vllm/llama.cpp; para CPU o caminho
        # é o FastLayoutPredictor (detector rf-detr "surya_layout2", o mesmo que o Marker usa por padrão).
        # Ele sobe um processo servidor local na primeira chamada.
        from surya.fast_layout import FastLayoutPredictor
        self.pred = FastLayoutPredictor(use_order=False)

    def predict(self, images):
        out = []
        for res in self.pred(images):
            out.append([{"label": b.label, "conf": float(getattr(b, "confidence", None) or 0.0), "bbox": list(b.bbox)}
                        for b in res.bboxes])
        return out


class DoclingLayout:
    name = "docling"

    def __init__(self, threads=None):
        # docling >= 2.13x: o layout é um "object-detection engine" genérico; usamos só ele, sem o pipeline
        # (sem parse de texto, OCR, tabelas). Modelo padrão: docling-layout-heron (RT-DETR v2).
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.pipeline_options import LayoutOptions
        from docling.models.stages.layout.layout_model import LayoutModel
        acc = AcceleratorOptions(device="cpu", num_threads=threads or os.cpu_count() or 4)
        self.model = LayoutModel(artifacts_path=None, accelerator_options=acc, options=LayoutOptions())
        self.engine = self.model.engine
        self.labels = self.engine.get_label_mapping()

    def predict(self, images):
        from docling.models.inference_engines.object_detection.base import ObjectDetectionEngineInput
        out = []
        for res in self.engine.predict_batch([ObjectDetectionEngineInput(image=im) for im in images]):
            out.append([{"label": self.labels.get(i, str(i)), "conf": float(s), "bbox": list(b)}
                        for i, s, b in zip(res.label_ids, res.scores, res.bboxes)])
        return out


BACKENDS = {"surya": SuryaLayout, "docling": DoclingLayout}
_cache = {}


def get_backend(name):
    if name not in _cache:
        _cache[name] = BACKENDS[name]()
    return _cache[name]


def is_figure(label):
    return str(label).lower().replace("-", "").replace("_", "") in FIG_LABELS


def _iou(a, b):
    iw, ih = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    i = max(0, iw) * max(0, ih)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def nms_figures(boxes, thr=0.5):
    """O detector do Docling devolve detecções cruas (sem o pós-processamento do pipeline): várias caixas de
    figura quase iguais. Mantém a de maior confiança entre as figuras que se sobrepõem."""
    keep = []
    for b in sorted(boxes, key=lambda x: -x["conf"]):
        if is_figure(b["label"]) and any(is_figure(k["label"]) and _iou(b["bbox_pdf"], k["bbox_pdf"]) > thr for k in keep):
            continue
        keep.append(b)
    return keep


def page_boxes(pg, backend="surya", min_conf=0.0, all_labels=False):
    """Caixas do modelo para uma página PyMuPDF. Devolve (boxes, segundos).

    boxes: [{"label", "conf", "bbox_pdf": [x0, y0, x1, y1]}] — só figuras, salvo all_labels=True."""
    be = get_backend(backend) if isinstance(backend, str) else backend
    img = render_page(pg)
    t0 = time.perf_counter()
    raw = be.predict([img])[0]
    dt = time.perf_counter() - t0
    boxes = [{"label": b["label"], "conf": round(b["conf"], 3), "bbox_pdf": _to_pdf(b["bbox"], img, pg)}
             for b in raw if (all_labels or is_figure(b["label"])) and b["conf"] >= min_conf]
    return nms_figures(boxes), dt


def run_document(pdf, out, backend="docling", min_conf=0.4, tabelas=True):
    """Prova inteira -> JSON {backend, em, segundos, pages: {"n": [boxes]}}. Escreve o progresso em <out>.progress
    ("feitas/total") para o servidor mostrar, e grava o resultado de forma atômica no fim."""
    import datetime
    doc = pymupdf.open(pdf)
    t0 = time.perf_counter()
    be = get_backend(backend)
    res = {"backend": backend, "min_conf": min_conf, "tabelas": tabelas, "pages": {}}
    prog = out + ".progress"
    for i in range(len(doc)):
        with open(prog, "w") as f:
            f.write(f"{i}/{len(doc)}")
        pg = doc[i]
        img = render_page(pg)
        raw = be.predict([img])[0]
        keep = [b for b in raw if b["conf"] >= min_conf and (is_figure(b["label"]) or (tabelas and str(b["label"]).lower() == "table"))]
        boxes = [{"label": b["label"], "conf": round(b["conf"], 3), "bbox_pdf": _to_pdf(b["bbox"], img, pg)} for b in keep]
        res["pages"][str(i + 1)] = nms_figures(boxes)
    res["segundos"] = round(time.perf_counter() - t0, 1)
    res["em"] = datetime.datetime.now().isoformat(timespec="seconds")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, out)
    if os.path.exists(prog):
        os.remove(prog)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("page", type=int, nargs="?", help="página 1-based (omita com --out para a prova inteira)")
    ap.add_argument("--backend", default="surya", choices=sorted(BACKENDS))
    ap.add_argument("--all", action="store_true", help="todas as classes, não só figura")
    ap.add_argument("--out", help="prova inteira: grava as caixas de todas as páginas neste JSON")
    ap.add_argument("--min-conf", type=float, default=0.4)
    a = ap.parse_args()
    if a.out:
        r = run_document(a.pdf, a.out, a.backend, a.min_conf)
        print(f"{sum(len(v) for v in r['pages'].values())} caixas em {r['segundos']} s")
        return
    doc = pymupdf.open(a.pdf)
    boxes, dt = page_boxes(doc[a.page - 1], a.backend, all_labels=a.all)
    print(json.dumps({"backend": a.backend, "seconds": round(dt, 2), "boxes": boxes}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
