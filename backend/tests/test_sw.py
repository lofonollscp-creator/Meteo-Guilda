"""La versión de caché del service worker debe corresponder al contenido de web/ (si no, los móviles se quedan con lo viejo)."""
import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("version_sw", RAIZ / "scripts" / "version_sw.py")
vsw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vsw)


def test_sw_tiene_la_version_del_contenido():
    assert vsw.actual() == vsw.version(), "ejecuta: python3 scripts/version_sw.py"


def test_la_version_cambia_con_el_contenido_y_no_con_su_propio_numero(tmp_path):
    (tmp_path / "sw.js").write_text('const V = "guilda-aaa";\n')
    (tmp_path / "a.js").write_text("uno")
    v1 = vsw.version(tmp_path)
    (tmp_path / "sw.js").write_text('const V = "guilda-bbb";\n')
    assert vsw.version(tmp_path) == v1
    (tmp_path / "a.js").write_text("dos")
    assert vsw.version(tmp_path) != v1
