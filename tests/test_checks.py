"""Schema validators as tests. Link checking is intentionally skipped (network)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"scripts_{name.replace('.py', '')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_dsa_passes():
    mod = load_script("check_dsa.py")
    assert mod.main() == 0


def test_roadmap_schemas_pass():
    mod = load_script("check_roadmaps.py")
    files = sorted(mod.ROADMAPS_DIR.glob("*.json"))
    assert files, "expected roadmap JSON files"
    errors = []
    for path in files:
        _, file_errors, _ = mod.validate_file(path)
        errors.extend(file_errors)
    assert errors == []
