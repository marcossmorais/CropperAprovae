"""Pasta de dados do programa instalado. Sem efeitos colaterais, a não ser migrar a pasta do nome antigo.

Até a v1.0.x o programa se chamava "System 4.0 Image" e guardava tudo em %LOCALAPPDATA%\\System4Image. Se essa pasta
existe e a nova (CropperAprovae) não, ela é renomeada: config.json, workspace e a IA instalada vêm juntos.
"""
import os

PASTA = "CropperAprovae"
PASTA_ANTIGA = "System4Image"


def data_dir():
    env = os.environ.get("CROPPERAPROVAE_DATA")
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    novo, antigo = os.path.join(base, PASTA), os.path.join(base, PASTA_ANTIGA)
    if not os.path.isdir(novo) and os.path.isdir(antigo):
        try:
            os.rename(antigo, novo)
        except OSError:
            return antigo  # em uso por outro programa: segue na pasta antiga em vez de perder a configuração
    return novo
