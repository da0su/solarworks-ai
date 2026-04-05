# config/__init__.py
# config.py (ルート) の内容を config/ パッケージからも参照できるようにフォワードする。
import os
import sys
from pathlib import Path

# ルートの config.py を importlib でロードしてこのパッケージに属性を注入する
import importlib.util as _ilu

_cfg_py = Path(__file__).resolve().parent.parent / "config.py"
if _cfg_py.exists():
    _spec = _ilu.spec_from_file_location("_coin_config_root", str(_cfg_py))
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    for _k in dir(_mod):
        if not _k.startswith("__"):
            globals()[_k] = getattr(_mod, _k)
