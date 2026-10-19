"""Avaliação do modo híbrido: figuras do engine (manifest revisado) x caixas de um modelo de layout.

Para cada prova do workspace, página a página, casa as figuras do engine com as caixas "Picture/Figure"
do modelo (layout_ai.py) e conta:
  - só o modelo viu       (candidata a figura perdida pelo engine)
  - só o engine viu       (candidata a falso positivo do engine, ou figura que o modelo não reconhece)
  - casadas com IoU < 0.7 (geometria divergente)
Usa o status da revisão humana do manifest como referência: crops "rejeitado" são falsos positivos
conhecidos do engine; "aprovado"/"herdado" são figuras confirmadas.

Só lê o workspace e o PDF; não escreve no manifest, no JSON nem no acervo.
Uso: .venv-ai/Scripts/python avaliar_hibrido.py --out <pasta> [--backends surya,docling] [--provas "201*_Prova_Dia*"]
"""
import argparse
import glob
import json
import os
import time

import pymupdf

import layout_ai

HERE = os.path.dirname(os.path.abspath(__file__))
IOU_OK = 0.7


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b):
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def iou(a, b):
    i = inter(a, b)
    return i / max(1e-6, area(a) + area(b) - i)


def overlap_min(a, b):
    """fração da menor caixa coberta pela outra (pega figura contida / caixa que engloba)."""
    return inter(a, b) / max(1e-6, min(area(a), area(b)))


def engine_figs(manifest):
    """Por página: figuras aceitas (bbox da figura, sem a margem do recorte), rejeitadas e ignoradas."""
    pages = {}
    seen = set()
    for c in manifest["crops"]:
        b = c.get("bbox_pdf") or c.get("rect_pdf")
        key = (c["page"], tuple(round(x) for x in b))
        if key in seen:  # mesma figura compartilhada por várias questões
            continue
        seen.add(key)
        kind = "rejeitado" if c.get("status") == "rejeitado" else "aceito"
        pages.setdefault(c["page"], {"aceito": [], "rejeitado": [], "ignorado": []})[kind].append(
            {"bbox": b, "role": c.get("role"), "status": c.get("status"), "q": c.get("questions"), "id": c.get("id")})
    for ig in manifest.get("ignored", []):
        pages.setdefault(ig["page"], {"aceito": [], "rejeitado": [], "ignorado": []})["ignorado"].append(
            {"bbox": ig["bbox"], "reason": ig.get("reason")})
    return pages


def match_page(eng, model):
    """Casamento guloso por IoU/sobreposição. Devolve dict com as categorias."""
    pairs = []
    for i, e in enumerate(eng):
        for j, m in enumerate(model):
            ov, u = overlap_min(e["bbox"], m["bbox_pdf"]), iou(e["bbox"], m["bbox_pdf"])
            if ov >= 0.5 or u >= 0.1:
                pairs.append((u, ov, i, j))
    pairs.sort(reverse=True)
    used_e, used_m, matched = set(), set(), []
    for u, ov, i, j in pairs:
        if i in used_e or j in used_m:
            continue
        used_e.add(i)
        used_m.add(j)
        matched.append({"engine": eng[i], "model": model[j], "iou": round(u, 3)})
    # agrupamento: várias figuras do engine (ex.: alternativas A–E) dentro de uma caixa só do modelo, ou o contrário
    grouped_e = [i for i, e in enumerate(eng) if i not in used_e and
                 any(inter(e["bbox"], m["bbox_pdf"]) >= 0.6 * area(e["bbox"]) for m in model)]
    grouped_m = [j for j, m in enumerate(model) if j not in used_m and
                 any(inter(m["bbox_pdf"], e["bbox"]) >= 0.6 * area(m["bbox_pdf"]) for e in eng)]
    return {
        "matched": matched,
        "low_iou": [x for x in matched if x["iou"] < IOU_OK],
        "only_engine": [e for i, e in enumerate(eng) if i not in used_e and i not in grouped_e],
        "only_model": [m for j, m in enumerate(model) if j not in used_m and j not in grouped_m],
        "grouped_engine": [eng[i] for i in grouped_e],
        "grouped_model": [model[j] for j in grouped_m],
    }


def contra_revisao(man, pn, model, s):
    """A discordância do modelo prevê onde o humano precisou mexer? Usa o histórico do manifest:
    - origem "auto" + "recorte manual": bbox_pdf é a figura original do engine, rect_pdf é o recorte do humano;
    - origem "manual": figura que o engine não achou e o humano recortou do zero."""
    for c in man["crops"]:
        if c["page"] != pn or c.get("status") == "rejeitado":
            continue
        human = c.get("rect_pdf")
        if c.get("origem") == "manual":
            s["humano_criou"] += 1
            s["humano_criou_modelo_viu"] += any(overlap_min(human, m["bbox_pdf"]) >= 0.5 for m in model)
            continue
        b = c.get("bbox_pdf")
        if not b or c.get("status") == "pendente":
            continue
        cands = [iou(b, m["bbox_pdf"]) for m in model if overlap_min(b, m["bbox_pdf"]) >= 0.5 or iou(b, m["bbox_pdf"]) >= 0.1]
        alert = not cands or max(cands) < IOU_OK
        if "recorte manual" in (c.get("flags") or []):
            # correção "grande": o humano cortou parte da figura do engine ou ampliou muito além da margem
            big = inter(b, human) < 0.8 * area(b) or area(b) < 0.35 * area(human)
            key = "fix_grande" if big else "fix_margem"
        else:
            key = "sem_fix"
        s[f"{key}_n"] += 1
        s[f"{key}_alerta"] += alert


def run_models(pdf, n_pages, backends, cache_path):
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
    doc = pymupdf.open(pdf)
    for be in backends:
        if be in cache and len(cache[be]["pages"]) == n_pages:
            continue
        t0 = time.perf_counter()
        model = layout_ai.get_backend(be)
        load = time.perf_counter() - t0
        res = {"load_s": round(load, 2), "pages": {}}
        for i in range(n_pages):
            boxes, dt = layout_ai.page_boxes(doc[i], model, all_labels=True)
            res["pages"][str(i + 1)] = {"seconds": round(dt, 3), "boxes": boxes}
            print(f"  {be} p{i + 1}: {dt:.2f}s, {sum(layout_ai.is_figure(b['label']) for b in boxes)} figuras", flush=True)
        cache[be] = res
        json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    return cache


def draw(pdf, page_n, eng, models, path, note=""):
    """PNG da página com engine (azul; rejeitado tracejado em cinza) e modelos (surya vermelho, docling verde)."""
    doc = pymupdf.open(pdf)
    pg = doc[page_n - 1]
    shape = pg.new_shape()
    for e in eng["aceito"]:
        shape.draw_rect(pymupdf.Rect(e["bbox"]))
        shape.finish(color=(0, 0.3, 1), width=2.5)
    for e in eng["rejeitado"]:
        shape.draw_rect(pymupdf.Rect(e["bbox"]))
        shape.finish(color=(0.5, 0.5, 0.5), width=1.5, dashes="[4 3] 0")
    colors = {"surya": (0.9, 0, 0), "docling": (0, 0.6, 0)}
    for be, boxes in models.items():
        off = 2.5 if be == "docling" else 0  # desloca um pouco para não sumir atrás da outra
        for b in boxes:
            x0, y0, x1, y1 = b["bbox_pdf"]
            shape.draw_rect(pymupdf.Rect(x0 - off, y0 - off, x1 + off, y1 + off))
            shape.finish(color=colors.get(be, (1, 0.5, 0)), width=1.5)
    shape.commit()
    legend = "azul=engine aceito  cinza=engine rejeitado  vermelho=surya  verde=docling" + (f"  | {note}" if note else "")
    pg.insert_text((10, 12), legend, fontsize=7, color=(0, 0, 0))
    pg.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3), alpha=False).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="pasta para cache, resultados e PNGs (fora do repo)")
    ap.add_argument("--backends", default="surya,docling")
    ap.add_argument("--provas", default="201[456]_Prova_Dia*")
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--tabelas", action="store_true", help="conta caixas 'Table' do modelo como figura (o engine recorta tabelas)")
    a = ap.parse_args()

    def is_fig(label):
        return layout_ai.is_figure(label) or (a.tabelas and str(label).lower() == "table")
    backends = [b for b in a.backends.split(",") if b]
    os.makedirs(a.out, exist_ok=True)
    summary, details = {}, []
    for wdir in sorted(glob.glob(os.path.join(HERE, "workspace", a.provas))):
        man = json.load(open(os.path.join(wdir, "manifest.json"), encoding="utf-8"))
        pid = man["prova_id"]
        print(f"[{pid}]", flush=True)
        n_pages = len(man["pages"])
        cache = run_models(man["pdf"], n_pages, backends, os.path.join(a.out, f"{pid}_layout.json"))
        eng_pages = engine_figs(man)
        for be in backends:
            s = summary.setdefault(be, {k: 0 for k in (
                "pages", "seconds", "load_s", "engine_aceitas", "modelo_figs", "casadas", "iou_baixo",
                "so_modelo", "so_modelo_formula", "so_modelo_margem", "so_engine", "agrupadas", "rejeitadas", "rejeitadas_modelo_viu",
                "humano_criou", "humano_criou_modelo_viu", "fix_grande_n", "fix_grande_alerta",
                "fix_margem_n", "fix_margem_alerta", "sem_fix_n", "sem_fix_alerta")})
            s["load_s"] = max(s["load_s"], cache[be]["load_s"])
            ious = s.setdefault("_ious", [])
            for pn in range(1, n_pages + 1):
                pr = cache[be]["pages"][str(pn)]
                s["pages"] += 1
                s["seconds"] += pr["seconds"]
                model = layout_ai.nms_figures([dict(b, kind=b["label"], label="Picture") for b in pr["boxes"]
                                               if is_fig(b["label"]) and b["conf"] >= a.min_conf])
                eng = eng_pages.get(pn, {"aceito": [], "rejeitado": [], "ignorado": []})
                r = match_page(eng["aceito"], model)
                # caixas "só do modelo" que o engine descartou de propósito (fórmula inline, carimbo) ou rejeitou na revisão
                only_m = []
                ph = man["pages"][pn - 1]["pdf_h"]
                for m in r["only_model"]:
                    if m["bbox_pdf"][3] < 0.09 * ph or m["bbox_pdf"][1] > 0.94 * ph:
                        s["so_modelo_margem"] += 1  # logo do cabeçalho / rodapé: o engine ignora de propósito
                    elif any(overlap_min(m["bbox_pdf"], ig["bbox"]) >= 0.5 for ig in eng["ignorado"]):
                        s["so_modelo_formula"] += 1
                    else:
                        only_m.append(m)
                contra_revisao(man, pn, model, s)
                rej_seen = [e for e in eng["rejeitado"] if any(overlap_min(e["bbox"], m["bbox_pdf"]) >= 0.5 for m in model)]
                s["engine_aceitas"] += len(eng["aceito"])
                s["modelo_figs"] += len(model)
                s["casadas"] += len(r["matched"])
                s["iou_baixo"] += len(r["low_iou"])
                s["so_modelo"] += len(only_m)
                s["so_engine"] += len(r["only_engine"])
                s["agrupadas"] += len(r["grouped_engine"]) + len(r["grouped_model"])
                s["rejeitadas"] += len(eng["rejeitado"])
                s["rejeitadas_modelo_viu"] += len(rej_seen)
                ious += [x["iou"] for x in r["matched"]]
                if only_m or r["only_engine"] or r["low_iou"]:
                    details.append({"prova": pid, "page": pn, "backend": be, "only_model": only_m,
                                    "only_engine": r["only_engine"], "low_iou": r["low_iou"],
                                    "rejeitadas_modelo_viu": rej_seen})
    for be, s in summary.items():
        ious = sorted(s.pop("_ious"))
        s["iou_mediana"] = ious[len(ious) // 2] if ious else None
        s["s_por_pagina"] = round(s["seconds"] / max(1, s["pages"]), 2)
        s["seconds"] = round(s["seconds"], 1)
    json.dump({"summary": summary, "details": details}, open(os.path.join(a.out, f"resultado_c{a.min_conf}{'_tab' if a.tabelas else ''}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
