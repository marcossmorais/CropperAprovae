# System 4.0 Image (PDFcropper)

Ferramenta local para recortar e revisar as figuras das questões de prova a partir do PDF.

- **Motor** (`engine.py`): lê o PDF com PyMuPDF, sem OCR e sem API. Acha as âncoras das questões e as figuras (imagens embutidas e desenhos vetoriais), associa cada figura à questão dona e recorta a 150 DPI.
- **Tela de revisão** (`server.py` + `static/index.html`): servidor HTTP local (só biblioteca padrão) em `http://127.0.0.1:8765`. Nela você importa o PDF, revisa os recortes, recorta à mão e exporta os aprovados para `Documentos/<Banca>/<Ano>/imagens`, atualizando o JSON da prova (com backup).

## Requisitos

- Python 3.11+
- `pip install -r requirements.txt`

## Uso

```bash
python server.py
```

No Windows, `abrir_system4_image.bat` faz o mesmo.

CLI do motor:

```bash
python engine.py <prova.pdf> --json <prova_LLM.json>
```

## Acervo

O motor lê e grava no acervo do System 3.0 (`Documentos/`, `logs/`). O caminho do acervo vem da variável `SYSTEM3_ROOT` (ex.: `C:\Users\marco\Documents\CLAUDE\System3.0`). Se ela não estiver definida, o `.bat` usa `%USERPROFILE%\Documents\CLAUDE\System3.0`.

A pasta `workspace/` guarda os recortes de trabalho e não vai para o repositório.
