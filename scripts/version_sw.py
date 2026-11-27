#!/usr/bin/env python3
"""Pone en web/sw.js una versión de caché derivada del contenido de web/ (así los móviles se bajan lo nuevo sin acordarse de subir el número a mano).
Uso:  python3 scripts/version_sw.py            reescribe web/sw.js si hace falta
      python3 scripts/version_sw.py --check    sale con 1 si la versión no corresponde al contenido (para tests/CI)"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
LINEA = re.compile(r'^const V = "guilda-[^"]*";', re.M)


def version(web: Path = WEB) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in web.rglob("*") if p.is_file()):
        datos = f.read_bytes()
        if f == web / "sw.js":
            datos = LINEA.sub('const V = "";', f.read_text(encoding="utf-8")).encode()      # el propio número no cuenta
        h.update(f.relative_to(web).as_posix().encode() + b"\0" + datos + b"\0")
    return "guilda-" + h.hexdigest()[:10]


def actual(web: Path = WEB) -> str | None:
    m = re.search(r'^const V = "(guilda-[^"]*)";', (web / "sw.js").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def main(argv: list[str]) -> int:
    nueva, vieja = version(), actual()
    if "--check" in argv:
        if nueva != vieja:
            print(f"web/sw.js tiene {vieja} y el contenido de web/ pide {nueva}: ejecuta python3 scripts/version_sw.py", file=sys.stderr)
        return int(nueva != vieja)
    if nueva != vieja:
        p = WEB / "sw.js"
        p.write_text(LINEA.sub(f'const V = "{nueva}";', p.read_text(encoding="utf-8"), count=1), encoding="utf-8")
        print(f"sw.js: {vieja} -> {nueva}")
    else:
        print(f"sw.js ya está en {nueva}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
