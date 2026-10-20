# CropperAprovae (PDFcropper)

Ferramenta local para recortar e revisar as figuras das questões de prova a partir do PDF.

- **Motor** (`engine.py`): lê o PDF com PyMuPDF, sem OCR e sem API. Acha as âncoras das questões e as figuras (imagens embutidas e desenhos vetoriais), associa cada figura à questão dona e recorta a 150 DPI.
- **Tela de revisão** (`server.py` + `static/index.html`): servidor HTTP local (só biblioteca padrão) em `http://127.0.0.1:8765`. Nela você importa o PDF, revisa os recortes, recorta à mão e exporta os aprovados para `Documentos/<Banca>/<Ano>/imagens`, atualizando o JSON da prova (com backup).

## Requisitos

- Python 3.11+
- `pip install -r requirements.txt` (PyMuPDF; `pywebview` é opcional e dá a janela própria)

## Uso

**Como aplicativo** (janela própria, sem terminal e sem navegador): `python criar_atalho.py` uma vez cria o atalho
"CropperAprovae" na Área de Trabalho e no menu Iniciar. Ele roda `pythonw app.py`, que sobe o servidor numa porta
livre dentro do próprio processo e abre uma janela nativa (WebView2, que já vem no Windows). Fechou a janela, fechou
tudo. O `abrir_cropperaprovae.bat` faz o mesmo. Erros ficam em `app.log`.

**Instalador** (o jeito mais simples): baixe o `CropperAprovae-Setup.exe` na página de
[Releases](https://github.com/marcossmorais/PDFcropper/releases) e abra. Uma janela pequena pergunta onde instalar e
qual é a pasta das provas, cria os atalhos (Área de Trabalho e menu Iniciar), confere o WebView2 (baixa o da Microsoft
se faltar) e, se você marcar, instala a **IA** num ambiente separado (PyTorch para CPU + Docling, ~1,5 GB; precisa de
internet e de Python 3.10+ no computador). Instala só para o seu usuário, sem pedir administrador, e aparece em
*Configurações → Aplicativos instalados* para desinstalar (a pergunta sobre apagar os dados vem com "Não" como padrão).
Para instalar sem janela: `CropperAprovae-Setup.exe --silencioso --dir PASTA --acervo PASTA [--sem-atalhos] [--ia]`.
Gerar: `python build_exe.py && python build_instalador.py` (sai em `dist/`).

**Como .exe** (sem precisar de Python, sem instalar nada): baixe o `CropperAprovae.exe` na página de
[Releases](https://github.com/marcossmorais/PDFcropper/releases) e abra. Os dados ficam em
`%LOCALAPPDATA%\CropperAprovae` (`config.json`, `app.log`, `workspace\`). No `config.json` dá para apontar:

- `"workspace"`: outra área de trabalho (por exemplo `C:\dev\PDFcropper\workspace`, para o .exe e o código-fonte
  verem as mesmas provas);
- `"acervo"`: a pasta do System 3.0, se não for `Documents\CLAUDE\System3.0`;
- `"ia_python"`: o Python do `.venv-ai`, para a segunda opinião por IA (ela não vai dentro do .exe).

Para gerar o .exe: crie um venv limpo `.venv-build` com `pymupdf` (mesma versão do Python principal), `pywebview` e
`pyinstaller`, e rode `python build_exe.py`. O resultado sai em `dist/CropperAprovae.exe` (~33 MB).

**No navegador** (modo de desenvolvimento): `python server.py` (porta 8765, ou `--port`/`PORT`).

### Telas

- **Provas**: lista os PDFs da pasta escolhida (botão *Escolher pasta…*, com pastas recentes), agrupados por
  subpasta, com o estado de cada um: questões no JSON, progresso da revisão e última gravação. *Buscar no acervo* e
  *Enviar PDF…* continuam como antes.
- **Revisão**: grade de 3 colunas; *Páginas* (tecla `P`) mostra a prova inteira ao lado com os recortes marcados
  (clicar num recorte seleciona o cartão e vice-versa; *✂ Recortar aqui* em cada página). *+ Recorte em outra
  questão* (tecla `N`) cria recorte para qualquer questão, inclusive as que não estão na grade.
- **Gravar no JSON…**: mostra antes de gravar o que muda em cada questão (no JSON agora × depois), a data de
  modificação e o último backup de cada JSON, a última gravação feita por aqui, e deixa escolher as questões e se
  espelha na prova irmã (OP1/OP2). O histórico fica no `manifest.json` (`exportacoes`).

### Segunda opinião por IA (opcional)

Um modelo de layout (Docling, licença MIT, roda local na CPU, sem custo de API) olha cada página e a tela avisa
quando ele discorda do motor: **possível figura sem recorte** e **geometria duvidosa** (só em recorte pendente). A IA
nunca recorta sozinha: *Usar área da IA* abre o recorte com a área marcada, e *Dispensar* esconde a sugestão. Roda
sozinha em segundo plano depois de processar uma prova (~1 s por página); o botão *Rodar IA* roda de novo.
Avaliação e regras: [`docs/avaliacao_hibrido.md`](docs/avaliacao_hibrido.md).

A aba **Motor × IA** mostra, para cada figura, o recorte do motor ao lado do recorte da IA, com *Usar motor*,
*Usar IA* ou *Recusar os dois* (e *Mudar* para desfazer). O recorte escolhido da IA vira um recorte aprovado com a
marca "área da IA".

Instalação (venv separado, porque o Docling puxa o PyTorch):

```bash
python -m venv .venv-ai
.venv-ai/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv-ai/Scripts/python -m pip install pymupdf docling
```

Sem o `.venv-ai`, o botão some e tudo funciona como antes.

CLI do motor:

```bash
python engine.py <prova.pdf> --json <prova_LLM.json>
```

## Acervo

O motor lê e grava no acervo do System 3.0 (`Documentos/`, `logs/`). O caminho do acervo vem da variável `SYSTEM3_ROOT` (ex.: `C:\Users\marco\Documents\CLAUDE\System3.0`). Se ela não estiver definida, o `.bat` usa `%USERPROFILE%\Documents\CLAUDE\System3.0`.

A pasta `workspace/` guarda os recortes de trabalho e não vai para o repositório.
