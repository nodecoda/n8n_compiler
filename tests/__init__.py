"""测试包 — 统一 sys.path 引导（唯一入口，测试文件不再自带 sys.path hack）。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
