"""Notificaciones push: validación de suscripciones (anti-SSRF), recordatorios de pasos, avisos sin repetir, envío y borrado de dispositivos caducados."""
from datetime import datetime, timedelta, timezone

from meteoguilda import avisos as avisos_mod
from meteoguilda import db, push
from tests.test_api import api  # noqa: F401  (fixture con Open-Meteo simulado)
from tests.test_v04 import cli  # noqa: F401  (cliente con BD, MG_QA y clave de cifrado)

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
SUB = {"endpoint": FCM, "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM", "auth": "tBHItJI5svbpez7KI4CCXg",
       "lat": 41.4923, "lon": 2.3611, "nombre": "Premià de Mar"}


def _limpia(monkeypatch):
    monkeypatch.setenv("MG_VAPID_PRIVATE_KEY", "x"); monkeypatch.setenv("MG_VAPID_PUBLIC_KEY", "BPUB")
    with db.pool().connection() as con:
        con.execute("TRUNCATE push_subs CASCADE")


def test_config_y_endpoints_solo_de_servicios_de_push(cli, monkeypatch):
    _limpia(monkeypatch)
    c = cli.get("/api/push/config").json()
    assert c["disponible"] and c["clave_publica"] == "BPUB"
    for malo in ["http://fcm.googleapis.com/x", "https://evil.example.com/x", "https://127.0.0.1/x", "https://169.254.169.254/x", "https://fcm.googleapis.com:8443/x",
                 "https://googleapis.com.evil.com/x", "ftp://fcm.googleapis.com/x"]:
        assert cli.put("/api/push/suscripcion", json={**SUB, "endpoint": malo}).status_code == 422, malo
    assert cli.put("/api/push/suscripcion", json={**SUB, "auth": "no valido!!"}).status_code == 422
    assert cli.put("/api/push/suscripcion", json=SUB).status_code == 200
    for ok in ["https://updates.push.services.mozilla.com/wpush/v2/x", "https://web.push.apple.com/x", "https://wns2-par02p.notify.windows.com/x"]:
        assert push.endpoint_ok(ok), ok


def test_suscribir_redondea_ubicacion_estado_y_baja(cli, monkeypatch):
    _limpia(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    e = cli.post("/api/push/estado", json={"endpoint": FCM}).json()
    assert e["suscrito"] and e["lat"] == 41.49 and e["lon"] == 2.36 and e["avisos"] and e["nivel_min"] == 2
    assert cli.post("/api/push/estado", json={"endpoint": FCM + "x"}).json() == {"suscrito": False}
    cli.request("DELETE", "/api/push/suscripcion", json={"endpoint": FCM})
    assert cli.post("/api/push/estado", json={"endpoint": FCM}).json()["suscrito"] is False


def test_maximo_de_dispositivos(cli, monkeypatch):
    _limpia(monkeypatch)
    for i in range(push.MAX_DISPOSITIVOS):
        assert cli.put("/api/push/suscripcion", json={**SUB, "endpoint": f"{FCM}{i}"}).status_code == 200
    assert cli.put("/api/push/suscripcion", json={**SUB, "endpoint": FCM + "extra"}).status_code == 409
    assert cli.put("/api/push/suscripcion", json={**SUB, "endpoint": FCM + "0"}).status_code == 200      # actualizar uno existente sí


def test_recordatorio_de_paso(cli, monkeypatch):
    _limpia(monkeypatch)
    aos = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    p = {"endpoint": FCM, "satelite": "ISS (ZARYA)", "aos": aos, "max_el": 63.4}
    assert cli.post("/api/push/paso", json=p).status_code == 404                  # sin dispositivo activado
    cli.put("/api/push/suscripcion", json=SUB)
    r = cli.post("/api/push/paso", json=p).json()
    assert r["nuevo"] is True and cli.post("/api/push/paso", json=p).json()["nuevo"] is False    # no se duplica
    with db.pool().connection() as con:
        f = con.execute("SELECT cuando, cuerpo FROM push_cola").fetchone()
    assert abs((f["cuando"] - (datetime.fromisoformat(aos) - timedelta(minutes=5))).total_seconds()) < 1 and "63°" in f["cuerpo"]
    assert cli.post("/api/push/paso", json={**p, "aos": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}).status_code == 422


async def test_enviar_solo_lo_vencido_y_borrar_dispositivos_caidos(cli, monkeypatch):
    _limpia(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB); cli.put("/api/push/suscripcion", json={**SUB, "endpoint": FCM + "muerto"})
    ahora = datetime.now(timezone.utc)
    with db.pool().connection() as con:
        push.encolar(con, FCM, "Ahora", "ya", clave="a"); push.encolar(con, FCM, "Luego", "después", cuando=ahora + timedelta(hours=1), clave="b")
        push.encolar(con, FCM + "muerto", "Ahora", "ya", clave="c")
    vistos = []

    def falso(s, payload):
        vistos.append((s["endpoint"], payload["titulo"]))
        return 410 if s["endpoint"].endswith("muerto") else 201
    monkeypatch.setattr(push, "_webpush", falso)
    assert await push.enviar_pendientes(ahora + timedelta(seconds=1)) == 1
    assert sorted(vistos) == [(FCM, "Ahora"), (FCM + "muerto", "Ahora")]
    assert cli.post("/api/push/estado", json={"endpoint": FCM + "muerto"}).json()["suscrito"] is False     # 410: el navegador se dio de baja
    assert await push.enviar_pendientes(ahora + timedelta(seconds=2)) == 0                                  # no se reenvía


async def test_avisos_nuevos_sin_repetir_y_con_nivel(cli, monkeypatch):
    _limpia(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cuadro = [(41.0, 2.0), (41.0, 3.0), (42.0, 3.0), (42.0, 2.0)]
    mk = lambda ev, nivel: {"evento": ev, "zona": "Maresme", "nivel": nivel, "desde": "2026-11-27T06:00:00+01:00", "hasta": "2026-11-27T18:00:00+01:00", "poligonos": [cuadro]}  # noqa: E731

    async def todos(http):
        return [mk("Lluvia", 1), mk("Tormentas", 2)]
    monkeypatch.setattr(avisos_mod, "todos", todos)
    assert await push.encolar_avisos(None) == 1                  # amarillo no llega con el nivel por defecto (naranja)
    assert await push.encolar_avisos(None) == 0                  # el mismo aviso no se repite
    with db.pool().connection() as con:
        f = con.execute("SELECT titulo, cuerpo FROM push_cola").fetchone()
    assert f["titulo"] == "Aviso naranja: Tormentas" and "Maresme" in f["cuerpo"] and "hasta 18:00" in f["cuerpo"]
    cli.put("/api/push/suscripcion", json={**SUB, "nivel_min": 1})
    assert await push.encolar_avisos(None) == 1                  # con «también amarillos» llega el de lluvia


async def test_sin_clave_de_aemet_no_hay_avisos_y_limpieza(cli, monkeypatch):
    _limpia(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)

    async def nada(http):
        return None
    monkeypatch.setattr(avisos_mod, "todos", nada)
    assert await push.encolar_avisos(None) == 0
    with db.pool().connection() as con:
        con.execute("UPDATE push_subs SET ultimo_ok = now() - interval '100 days'")
    push.limpiar()
    assert cli.post("/api/push/estado", json={"endpoint": FCM}).json()["suscrito"] is False


def test_borrar_cuenta_borra_dispositivos_y_se_exportan(cli, monkeypatch):
    _limpia(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    d = cli.get("/api/me/datos").json()["notificaciones"]
    assert d[0]["servicio_push"] == "fcm.googleapis.com" and d[0]["ubicacion_aprox"] == [41.49, 2.36] and "auth" not in str(d)
    assert cli.delete("/api/me").status_code == 204
    assert cli.post("/api/push/estado", json={"endpoint": FCM}).json()["suscrito"] is False
