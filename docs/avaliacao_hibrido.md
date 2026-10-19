# Avaliação: modelo de layout como "segunda opinião" do engine

Data: 2026-10-19. Protótipo: `layout_ai.py` (backends) + `avaliar_hibrido.py` (comparação).
Corpus: as 6 provas ENEM do `workspace/` (2014–2016, Dia 1 e Dia 2), 192 páginas, só CPU (24 threads, sem GPU).
Nada foi alterado em `engine.py`, no export, nos manifests nem em `Documentos/`.

## Como foi medido

- **Engine** = figuras do `manifest.json` (já revisado na UI): `bbox_pdf` dos crops automáticos, `rect_pdf` dos recortes
  manuais. Crops `rejeitado` ficam separados.
- **Modelos**, só o detector de layout, sem OCR e sem pipeline:
  - **Surya**: `surya-ocr` 0.22.1, `FastLayoutPredictor` (rf-detr `datalab-to/surya_layout2`). É o caminho de CPU do
    Marker. O `LayoutPredictor` "cheio" do Surya 0.22 virou um VLM que exige servidor vllm/llama.cpp, por isso ficou de fora.
  - **Docling**: `docling` 2.140, motor de object detection do `LayoutModel` (`docling-layout-heron`, RT-DETR v2),
    chamado direto, sem o `DocumentConverter`.
- Classes contadas como figura: Picture/Figure/Diagram/Chart/ChemicalBlock e **Table**, porque o engine recorta tabelas
  e o usuário as aprova. Sem Table, o Docling deixaria de ver 32 figuras aceitas em vez de 2. Caixas do mesmo tipo que
  se sobrepõem passam por NMS (o detector cru do Docling devolve duplicatas).
- Casamento por página: guloso por IoU, aceitando o par com IoU ≥ 0.1 ou ≥ 50% da caixa menor coberta. Quando várias
  figuras do engine (por exemplo, as alternativas A–E) caem dentro de uma caixa só do modelo, ou o contrário, o caso
  conta como "agrupada" e não como discordância.
- Fora da contagem de "só modelo": logo, código de barras e rodapé (faixa de 9% no topo e 6% embaixo), e caixas em
  cima de fórmulas que o engine já ignora de propósito.
- Limiar de confiança: 0.4 para o Surya (o padrão dele) e 0.5 para o Docling. A grade completa (0.4 a 0.7) está no
  fim do documento.

## Números

| | Surya (rf-detr) | Docling (heron) |
|---|---:|---:|
| s/página (inferência, CPU) | **0.42** | 0.99 |
| carga do modelo | ~4 s (+ ~60 s na 1ª vez: sobe um processo servidor e baixa os pesos) | ~15 s |
| figuras aceitas do engine | 256 | 256 |
| caixas de figura do modelo | 307 | 827 |
| casadas | 210 | 239 |
| casadas com IoU < 0.7 | 81 | 82 |
| IoU mediano das casadas | 0.81 | 0.80 |
| **só o engine viu** | 18 | **2–3** |
| **só o modelo viu** (fora cabeçalho/fórmula) | 47 | 122 |
| agrupadas (A–E numa caixa só etc.) | 42 | 43 |
| crops rejeitados pelo humano que o modelo também "vê" | 32 / 44 | 37 / 44 |

### Contra a revisão humana (o que importa para "segunda opinião")

O manifest guarda o histórico: um crop `auto` com flag "recorte manual" tem a figura original do engine em `bbox_pdf`
e o recorte do humano em `rect_pdf`; um crop `origem: manual` é uma figura que o engine não achou. Um **alerta** é
"o modelo não tem caixa para essa figura do engine" ou "IoU < 0.7".

| | Surya @0.4 | Docling @0.5 |
|---|---:|---:|
| figuras que o **humano recortou do zero** (engine perdeu) e o modelo viu | 83 / 92 (90%) | **91 / 92 (99%)** |
| correções grandes de geometria (humano cortou parte ou ampliou muito) com alerta | **13 / 14** | 9 / 14 |
| ajustes de margem com alerta | 16 / 55 | 13 / 55 |
| crops que ninguém corrigiu com alerta (alarme falso) | 17 / 57 (30%) | **11 / 57 (19%)** |

Uma ressalva sobre o "engine perdeu": parte dos 92 recortes manuais são alternativas A–E que o engine juntou num
bloco só ou deixou de separar. O modelo "ver" a região não quer dizer que ele as separa. O Docling às vezes também
agrupa, como na Q68 de 2015 D1.

## Exemplos (PNGs no scratchpad da sessão, fora do repo)

Pasta: `%TEMP%\claude\C--Users-marco-Documents-CLAUDE-System3-0\17d98369-…\scratchpad\exemplos\`.
Cores: azul = engine aceito, cinza tracejado = engine rejeitado, vermelho = Surya, verde = Docling.

1. **2016 D1 p22**: na Q61 o engine pegou só o cabeçalho cinza da tabela e os dois modelos pegaram a tabela inteira;
   na Q63 a figura automática virou só a parábola da alternativa E. O humano corrigiu os dois à mão, e os dois modelos
   discordavam do engine (IoU 0.05–0.14). É exatamente o alerta que valeria a pena.
2. **2015 D1 p30**: no esquema das torres de celular (Q88), o Docling dá uma caixa que bate com o engine, enquanto o
   Surya fragmenta em 6 ícones. Isso mostra a fraqueza do Surya em figuras compostas de peças soltas.
3. **2015 D1 p24**: no circuito da Q68, o engine separou A–E e o Docling juntou tudo numa caixa e perdeu a legenda
   "Símbolos adotados". No gráfico da Q69, o Surya inclui o rótulo do eixo x, que o `bbox_pdf` do engine deixa de fora.
4. **2014 D2 p14**: a imagem "VIVA A NOVA TV" (Q121) é um bloco de texto em imagem. Os dois modelos a classificam como
   Text e não a veem. Esse é o ponto cego: imagem com muito texto.
5. **2016 D2 p31**: na Q178, os dois modelos veem as cinco cadeiras das alternativas e o engine não recortou nenhuma.
   **Achado à parte:** o JSON em Documentos tem `178_A..E.png` e não tem a cadeira do enunciado, mas o engine marcou a
   cadeira como "herdado" ("questão já tem imagem no JSON"). Vale conferir: a imagem do enunciado pode estar faltando.
6. **2016 D2 p21**: as frações das alternativas da Q147 (vetor) viram "picture" no Docling. É ruído típico do "só modelo".
7. **2015 D2 p2** (proposta de redação): o Docling acha 11 figuras e o Surya 6. O engine ignora a página porque ela não
   tem questões, e isso está correto.
8. **2015 D1 p25**: o Docling marca como figura 10 ícones de ~10 pt (marcadores). Um filtro de tamanho mínimo resolve.
9. **2014 D1 p22** e **2016 D1 p23**: casos de IoU baixo em que os modelos incluem legenda ou fonte, ou fatiam linhas
   de texto como figura (Surya).

## Recomendação

**Vale plugar o Docling como segunda opinião, só como alerta na tela de revisão.** Ele não deve recortar sozinho nem
substituir a geometria do PyMuPDF.

- **Docling (heron) em vez de Surya.** Ele viu 99% das figuras que o humano teve de adicionar e deixou de ver só 2–3
  das 256 aceitas. Também tem menos alarme falso nos crops bons (19% contra 30%) e a licença é limpa: código MIT e
  pesos Apache-2.0. O Surya é 2× mais rápido e sinalizou melhor as correções grandes de geometria (13/14 contra 9/14),
  mas fragmenta figuras compostas, perde 10% das figuras manuais e roda como processo servidor separado. A licença dele
  também pesa: código GPL-3 e pesos OpenRAIL-M com teto de US$5M de receita ou funding para uso comercial.
- **Custo:** ~1 s por página na CPU, ou seja ~30 s por prova de 32 páginas mais 15 s de carga. Dá para rodar em todas
  as páginas em background ao processar a prova. Não precisa restringir às páginas com alerta para economizar; a
  restrição serve para **reduzir o ruído** mostrado ao usuário.
- **Regras de alerta sugeridas** (os números acima dizem que funcionam):
  1. "Possível figura não recortada": caixa Docling com conf ≥ 0.7 e ≥ 40 pt, fora da faixa de cabeçalho/rodapé, sem
     sobrepor nenhum crop nem item `ignored`, numa página que tem âncora de questão. Isso elimina redação, ícones e logo.
  2. "Geometria duvidosa": figura do engine com IoU < 0.5 contra a melhor caixa Docling, ou figura sem caixa
     correspondente. Mostrar a caixa do modelo como sugestão de recorte, com um clique para aplicar via `recrop`.
  3. Não usar o modelo para validar rejeições: ele "vê" 37 dos 44 crops que o humano rejeitou. Esses crops costumam
     ser figuras reais rejeitadas por outro motivo (duplicata, questão errada), não falsos positivos do detector.
- **PDF escaneado:** é onde o modelo vira o motor principal, porque o PyMuPDF não tem nada para ancorar. Não testei,
  já que o corpus ENEM é nativo. O próximo passo seria pegar um escaneado do acervo e medir com o mesmo script. Lá a
  âncora "73 –" teria de vir de OCR, o que é outro projeto.

## Reproduzir

```bash
python -m venv .venv-ai
.venv-ai/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv-ai/Scripts/python -m pip install pymupdf docling openai platformdirs
.venv-ai/Scripts/python -m pip install --no-deps surya-ocr==0.22.1   # o surya fixa pillow<11, sem wheel para Python 3.14
PYTHONIOENCODING=utf-8 .venv-ai/Scripts/python avaliar_hibrido.py --out <pasta fora do repo> --min-conf 0.5 --tabelas
```

Na primeira rodada as caixas de cada prova ficam em cache (`<prova>_layout.json`). Mudar limiar ou regra de
casamento depois disso leva segundos. O `.venv-ai` instala `pymupdf` 2.0, contra o 1.28 do ambiente principal; o
script de avaliação não importa o `engine.py`.

### Grade de limiares (com Table)

| conf | Surya: só engine / só modelo / alarme falso / humano-criou visto | Docling: idem |
|---|---|---|
| 0.4 | 18 / 47 / 17 de 57 / 83 de 92 | 2 / 215 / 11 de 57 / 92 de 92 |
| 0.5 | 64 / 35 / 22 de 57 / 56 de 92 | 3 / 122 / 11 de 57 / 91 de 92 |
| 0.7 | 134 / 20 / 36 de 57 / 35 de 92 | 7 / 46 / 16 de 57 / 90 de 92 |

O Docling é estável entre 0.5 e 0.7, o que deixa usar 0.7 no alerta de "figura não recortada" sem perder recall. O
Surya despenca acima de 0.4.
