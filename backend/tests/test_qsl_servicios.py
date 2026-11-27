"""Cuaderno conectado con LoTW, eQSL y QRZ, probado contra servidores simulados (nunca contra los reales)."""
import base64
import gzip
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from meteoguilda import db, logbook, qsl_servicios
from tests.test_api import api  # noqa: F401
from tests.test_v04 import cli  # noqa: F401

T0 = datetime(2026, 11, 20, 18, 30, tzinfo=timezone.utc)


@pytest.fixture
def cuaderno(cli, monkeypatch):
    with db.pool().connection() as con:
        con.execute("TRUNCATE qsl_credenciales, profiles CASCADE")
    key = logbook.create_profile()
    h = logbook.auth(key)
    logbook.add_many(h, [logbook.clean({"call": "W1AW", "qso_at": T0, "band": "20m", "mode": "SSB", "freq_mhz": 14.2}),
                         logbook.clean({"call": "EA3XYZ", "qso_at": T0 + timedelta(hours=1), "band": "40m", "mode": "FT8"}),
                         logbook.clean({"call": "F4ABC", "qso_at": T0 + timedelta(hours=2), "band": "2m", "mode": "FM"})])
    return {"Authorization": f"Bearer {key}"}, h


def _servidor(monkeypatch, handler):
    llamadas = []

    def h(req: httpx.Request):
        llamadas.append(req)
        return handler(req)
    monkeypatch.setattr(qsl_servicios, "cliente", lambda request: httpx.AsyncClient(transport=httpx.MockTransport(h)))
    return llamadas


def _qsos(h):
    return {q["call"]: q for q in logbook.list_qsos(h)}


def test_desactivado_sin_credenciales_y_no_llama_a_nadie(cli, cuaderno, monkeypatch):
    hdr, _ = cuaderno
    llamadas = _servidor(monkeypatch, lambda r: httpx.Response(500))
    s = cli.get("/api/qsl/servicios").json()
    assert not any(v["conectado"] for v in s["servicios"].values()) and s["puede_guardar"]
    for srv in ("lotw", "eqsl", "qrz"):
        assert cli.post(f"/api/qsl/{srv}/subir", headers=hdr).status_code == 409
        assert cli.post(f"/api/qsl/{srv}/confirmaciones", headers=hdr).status_code == 409
    assert llamadas == []
    assert cli.post("/api/qsl/qrz/subir").status_code == 401                       # sin la clave del cuaderno


def test_credenciales_cifradas_nunca_se_devuelven_ni_por_identidad_delegada(cli, monkeypatch):
    with db.pool().connection() as con:
        con.execute("TRUNCATE qsl_credenciales")
    assert cli.put("/api/qsl/servicios/qrz", json={"api_key": "malformada"}).status_code == 422
    r = cli.put("/api/qsl/servicios/qrz", json={"api_key": "abcd-1234-EFGH-5678"}).json()
    assert r["servicios"]["qrz"]["conectado"] and r["servicios"]["qrz"]["clave"] == "••••5678"
    assert "1234" not in str(r) and "ABCD" not in str(r)
    with db.pool().connection() as con:
        fila = con.execute("SELECT cifrado FROM qsl_credenciales").fetchone()
    assert "ABCD-1234" not in fila["cifrado"]
    # el MCP actúa con X-Admin-Token + X-Act-As: puede ver el estado, pero no guardar ni borrar credenciales
    monkeypatch.setenv("MG_ADMIN_TOKEN", "ci-admin")
    delegada = {"X-Admin-Token": "ci-admin", "X-Act-As": "qa"}
    assert cli.put("/api/qsl/servicios/eqsl", json={"usuario": "EA3X", "password": "x"}, headers=delegada).status_code == 403
    assert cli.delete("/api/qsl/servicios/qrz", headers=delegada).status_code == 403
    assert cli.get("/api/qsl/servicios", headers=delegada).json()["servicios"]["qrz"]["conectado"]
    assert cli.get("/api/me/datos").json()["servicios_qsl"][0]["servicio"] == "qrz"
    assert cli.delete("/api/qsl/servicios/qrz").json()["servicios"]["qrz"]["conectado"] is False


def test_qrz_sube_y_confirma(cli, cuaderno, monkeypatch):
    hdr, h = cuaderno
    cli.put("/api/qsl/servicios/qrz", json={"api_key": "ABCD-1234-EFGH-5678"})

    def qrz(req):
        f = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        assert f["KEY"] == "ABCD-1234-EFGH-5678" and str(req.url) == qsl_servicios.URLS["qrz"]
        if f["ACTION"] == "INSERT":
            if "<CALL:6>EA3XYZ" in f["ADIF"]:
                return httpx.Response(200, text="RESULT=FAIL&REASON=Unable to add QSO to database: duplicate&COUNT=0")
            if "<CALL:5>F4ABC" in f["ADIF"]:
                return httpx.Response(200, text="RESULT=FAIL&REASON=wrong band&COUNT=0")
            return httpx.Response(200, text="RESULT=OK&LOGID=1&COUNT=1")
        assert "STATUS:CONFIRMED" in f["OPTION"]
        adif = "<CALL:4>W1AW<QSO_DATE:8>20261120<TIME_ON:4>1840<BAND:3>20M<QSL_RCVD:1>Y<EOR><CALL:4>ZZ9Z<QSO_DATE:8>20261120<TIME_ON:4>1840<EOR>"
        return httpx.Response(200, text="RESULT=OK&COUNT=2&ADIF=" + adif.replace("<", "&lt;").replace(">", "&gt;").replace("&", "%26"))
    llamadas = _servidor(monkeypatch, qrz)
    r = cli.post("/api/qsl/qrz/subir", headers=hdr).json()
    assert r["enviados"] == 2 and r["con_error"] == 1 and "wrong band" in r["errores"][0] and r["pendientes"] == 1
    assert len(llamadas) == 3
    r = cli.post("/api/qsl/qrz/subir", headers=hdr).json()                      # solo reintenta el que falló
    assert len(llamadas) == 4 and r["enviados"] == 0
    c = cli.post("/api/qsl/qrz/confirmaciones", headers=hdr).json()
    assert c == {"descargadas": 2, "confirmados_nuevos": 1, "sin_pareja": 1}
    q = _qsos(h)
    assert q["W1AW"]["qsl_rcvd"] and q["W1AW"]["servicios"]["qrz"]["confirmado"] and q["W1AW"]["servicios"]["qrz"]["enviado"]
    assert not q["EA3XYZ"]["qsl_rcvd"]
    assert cli.post("/api/qsl/qrz/confirmaciones", headers=hdr).json()["confirmados_nuevos"] == 0      # no se cuenta dos veces
    assert cli.get("/api/qsl/servicios").json()["servicios"]["qrz"]["confirmadas_ultima"] == 0


def test_qrz_clave_rechazada(cli, cuaderno, monkeypatch):
    hdr, _ = cuaderno
    cli.put("/api/qsl/servicios/qrz", json={"api_key": "ABCD-1234-EFGH-5678"})
    _servidor(monkeypatch, lambda r: httpx.Response(200, text="RESULT=AUTH&REASON=invalid api key"))
    r = cli.post("/api/qsl/qrz/subir", headers=hdr)
    assert r.status_code == 409 and "clave" in r.json()["detail"]
    assert "clave" in cli.get("/api/qsl/servicios").json()["servicios"]["qrz"]["ultimo_error"]


def test_eqsl_sube_y_baja_buzon(cli, cuaderno, monkeypatch):
    hdr, h = cuaderno
    assert cli.put("/api/qsl/servicios/eqsl", json={"usuario": "EA3TEST"}).status_code == 422
    cli.put("/api/qsl/servicios/eqsl", json={"usuario": "EA3TEST", "password": "secreta", "qth_nick": "Casa"})

    def eqsl(req):
        u = str(req.url)
        if u.startswith(qsl_servicios.URLS["eqsl_subir"]):
            datos = parse_qs(req.content.decode())["ADIFData"][0]
            assert "<EQSL_USER:7>EA3TEST<EQSL_PSWD:7>secreta" in datos and "<APP_EQSL_QTH_NICKNAME:4>Casa" in datos and datos.count("<EOR>") == 3
            return httpx.Response(200, text="<HTML><BODY>Result: 2 out of 3 records added<BR>Warning: Y=2026 M=11 D=20 EA3XYZ 40M FT8 Bad record: Duplicate<BR></BODY></HTML>")
        if u.startswith(qsl_servicios.URLS["eqsl_bajar"]):
            assert req.url.params["UserName"] == "EA3TEST" and req.url.params["QTHNickname"] == "Casa"
            return httpx.Response(200, text='<HTML>Your ADIF log file has been built. <A HREF="../downloadedfiles/abc123.adi">.ADI file</A></HTML>')
        assert u == "https://www.eqsl.cc/downloadedfiles/abc123.adi"
        return httpx.Response(200, text="eQSL inbox<EOH>\n<CALL:5>F4ABC<QSO_DATE:8>20261120<TIME_ON:4>2025<BAND:2>2M<MODE:2>FM<EOR>")
    _servidor(monkeypatch, eqsl)
    r = cli.post("/api/qsl/eqsl/subir", headers=hdr).json()
    assert r["enviados"] == 3 and r["pendientes"] == 0                          # el duplicado ya estaba en eQSL: cuenta como subido
    c = cli.post("/api/qsl/eqsl/confirmaciones", headers=hdr).json()
    assert c["confirmados_nuevos"] == 1 and _qsos(h)["F4ABC"]["qsl_rcvd"], c


def test_eqsl_contraseña_mala(cli, cuaderno, monkeypatch):
    hdr, _ = cuaderno
    cli.put("/api/qsl/servicios/eqsl", json={"usuario": "EA3TEST", "password": "mala"})
    _servidor(monkeypatch, lambda r: httpx.Response(200, text="<HTML>Error: No match on eQSL_User/eQSL_Pswd</HTML>"))
    assert cli.post("/api/qsl/eqsl/subir", headers=hdr).status_code == 409
    assert cli.post("/api/qsl/eqsl/confirmaciones", headers=hdr).status_code == 409


def _p12(password=b"clave"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EA3TEST")])
    c = (x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key()).serial_number(1)
         .not_valid_before(datetime(2025, 1, 1)).not_valid_after(datetime(2030, 1, 1)).sign(k, hashes.SHA256()))
    p12 = pkcs12.serialize_key_and_certificates(b"ea3test", k, c, None, serialization.BestAvailableEncryption(password))
    return base64.b64encode(p12).decode(), c


def test_lotw_firma_cada_contacto_y_confirma(cli, cuaderno, monkeypatch):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    hdr, h = cuaderno
    p12, cert = _p12()
    base = {"usuario": "ea3test", "password": "web"}
    assert cli.put("/api/qsl/servicios/lotw", json=base).json()["servicios"]["lotw"]["puede_subir"] is False
    assert cli.post("/api/qsl/lotw/subir", headers=hdr).status_code == 409                 # sin certificado no se sube
    assert cli.put("/api/qsl/servicios/lotw", json={**base, "p12_b64": p12, "p12_password": "otra"}).status_code == 422
    assert cli.put("/api/qsl/servicios/lotw", json={**base, "p12_b64": p12, "p12_password": "clave"}).status_code == 422      # falta la estación
    est = {"indicativo": "EA3TEST", "dxcc": 281, "grid": "JN11cj", "cqz": 14, "ituz": 37}
    assert cli.put("/api/qsl/servicios/lotw", json={**base, "p12_b64": p12, "p12_password": "clave", **est}).json()["servicios"]["lotw"]["puede_subir"]
    vistos = {}

    def lotw(req):
        if str(req.url) == qsl_servicios.URLS["lotw_subir"]:
            m = re.search(rb'filename="([^"]+)".*?\r\n\r\n(.*)\r\n--', req.content, re.S)
            assert m.group(1).endswith(b".tq8")
            g = gzip.decompress(m.group(2)).decode()
            assert "<Rec_Type:5>tCERT" in g and "<DXCC:3>281" in g and "<GRIDSQUARE:4>JN11" in g
            for sig, data in re.findall(r"<SIGN_LOTW_V2\.0:\d+:6>(.*?)\n<SIGNDATA:\d+>(.*?)\n", g, re.S):
                cert.public_key().verify(base64.b64decode(sig), data.encode(), padding.PKCS1v15(), hashes.SHA1())      # firma válida
                vistos[data] = True
            return httpx.Response(200, text="<html><!-- .UPL. accepted --><!-- .UPLMESSAGE. File queued for processing --></html>")
        assert req.url.params["login"] == "ea3test" and req.url.params["qso_qsl"] == "yes"
        return httpx.Response(200, text="ARRL Logbook of the World Status Report\n<eoh>\n<CALL:4>W1AW<BAND:3>20M<QSO_DATE:8>20261120<TIME_ON:6>183000<QSL_RCVD:1>Y<eor>")
    _servidor(monkeypatch, lotw)
    assert cli.post("/api/qsl/lotw/subir", headers=hdr).json()["enviados"] == 3
    assert "14JN113720MW1AW14.2SSB2026-11-2018:30:00Z" in vistos and len(vistos) == 3
    assert cli.post("/api/qsl/lotw/confirmaciones", headers=hdr).json()["confirmados_nuevos"] == 1
    assert _qsos(h)["W1AW"]["servicios"]["lotw"]["confirmado"]


def test_lotw_rechazo_y_contraseña_web_mala(cli, cuaderno, monkeypatch):
    hdr, _ = cuaderno
    p12, _ = _p12()
    cli.put("/api/qsl/servicios/lotw", json={"usuario": "ea3test", "password": "web", "p12_b64": p12, "p12_password": "clave",
                                             "indicativo": "EA3TEST", "dxcc": 281, "grid": "JN11", "cqz": 14, "ituz": 37})
    _servidor(monkeypatch, lambda r: httpx.Response(200, text="<!-- .UPL. rejected --><!-- .UPLMESSAGE. Signature verification failed -->")
              if r.method == "POST" else httpx.Response(200, text="<html>Username/password incorrect</html>"))
    r = cli.post("/api/qsl/lotw/subir", headers=hdr).json()
    assert r["enviados"] == 0 and "Signature verification failed" in r["errores"][0] and r["pendientes"] == 3
    assert cli.post("/api/qsl/lotw/confirmaciones", headers=hdr).status_code == 409


async def test_mcp_sube_con_la_cuenta_y_no_expone_credenciales(cli, cuaderno, monkeypatch):
    from meteoguilda import main, mcp_server
    hdr, h = cuaderno
    cli.put("/api/qsl/servicios/qrz", json={"api_key": "ABCD-1234-EFGH-5678"})
    nombres = {t.name for t in await mcp_server.mcp.list_tools()}
    assert {"qsl_services_status", "qsl_upload", "qsl_import_confirmations"} <= nombres
    assert not any("credencial" in n or "password" in n for n in nombres)
    _servidor(monkeypatch, lambda r: httpx.Response(200, text="RESULT=OK&LOGID=1&COUNT=1"))
    monkeypatch.setenv("MG_ADMIN_TOKEN", "ci-admin")
    monkeypatch.setattr(mcp_server, "_who", lambda ctx: {"sub": "qa", "email": "qa@local"})
    monkeypatch.setattr(mcp_server, "_client", httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t"))
    r = str(await mcp_server.mcp.call_tool("qsl_upload", {"service": "qrz", "key": hdr["Authorization"][7:]}))
    assert "'enviados': 3" in r.replace('"', "'")
    st = str(await mcp_server.mcp.call_tool("qsl_services_status", {}))
    assert "5678" in st and "ABCD-1234" not in st
