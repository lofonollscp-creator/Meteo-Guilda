"""Cuota de Open-Meteo (plan gratuito: 600 por minuto, 5.000 por hora y 10.000 por día).
Cuenta cada petición saliente a *.open-meteo.com y, si la fuente responde 429, frena las siguientes hasta que pase el límite que dijo
(minuto, hora o día) para no insistir ni empeorarlo: los llamadores ya caen a los datos guardados cuando la petición falla."""
from __future__ import annotations

import time
from collections import Counter, deque

import httpx

LIMITES = {"minuto": 600, "hora": 5000, "dia": 10000}


class CuotaAgotada(httpx.TransportError):
    """Open-Meteo ha dicho que se alcanzó su límite: no se vuelve a llamar hasta `hasta`."""


class Cuota:
    def __init__(self) -> None:
        self.eventos: deque[tuple[float, str]] = deque()      # (instante, endpoint) de las últimas 24 h
        self.hasta = 0.0                                      # epoch hasta el que se frena
        self.motivo = ""
        self.rechazadas = 0                                   # 429 recibidos desde el arranque
        self.frenadas = 0                                     # peticiones evitadas por el freno

    @staticmethod
    def endpoint(url: str) -> str:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        ruta = url.split(host, 1)[-1].split("?", 1)[0]
        return (host.split(".open-meteo.com")[0] + ruta) if host.endswith("open-meteo.com") else ""

    def _purga(self, now: float) -> None:
        while self.eventos and now - self.eventos[0][0] > 86400:
            self.eventos.popleft()

    def antes(self, url: str, now: float | None = None) -> None:
        """Se llama antes de cada petición. Cuenta las de Open-Meteo y lanza CuotaAgotada si hay freno activo."""
        ep = self.endpoint(url)
        if not ep:
            return
        now = time.time() if now is None else now
        if now < self.hasta:
            self.frenadas += 1
            raise CuotaAgotada(f"Open-Meteo alcanzó su límite ({self.motivo}); se reintenta a las {time.strftime('%H:%M', time.localtime(self.hasta))}")
        self._purga(now)
        self.eventos.append((now, ep))

    def limite_alcanzado(self, cuerpo: str, now: float | None = None) -> float:
        """Registra un 429. Por el texto de Open-Meteo («Minutely/Hourly/Daily API request limit exceeded») decide cuánto frenar."""
        now = time.time() if now is None else now
        t = (cuerpo or "").lower()
        if "daily" in t or "diari" in t:
            espera, self.motivo = 86400 - (now % 86400) + 60, "diario"      # hasta pasada la medianoche UTC, cuando se reinicia
        elif "hourly" in t:
            espera, self.motivo = 3600 - (now % 3600) + 30, "por hora"
        else:
            espera, self.motivo = 65, "por minuto"
        self.hasta = max(self.hasta, now + espera)
        self.rechazadas += 1
        return self.hasta

    def estado(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self._purga(now)
        n = lambda seg: sum(1 for t, _ in self.eventos if now - t <= seg)  # noqa: E731
        uso = {"minuto": n(60), "hora": n(3600), "dia": len(self.eventos)}
        por = Counter(ep for t, ep in self.eventos if now - t <= 3600)
        return {"peticiones": uso, "limites": LIMITES, "porcentaje": {k: round(100 * uso[k] / LIMITES[k], 1) for k in uso},
                "ultima_hora_por_servicio": dict(por.most_common()), "frenado": now < self.hasta,
                "frenado_hasta": self.hasta if now < self.hasta else None, "motivo": self.motivo if now < self.hasta else None,
                "rechazadas_429": self.rechazadas, "peticiones_evitadas": self.frenadas,
                "nota": "Cuenta peticiones HTTP (no su peso: una con muchas variables, modelos y días cuenta más para Open-Meteo, así que su límite real "
                        "se alcanza antes). Se reinicia al reiniciar el servicio."}


cuota = Cuota()


async def hook_antes(request: httpx.Request) -> None:
    cuota.antes(str(request.url))


async def hook_despues(response: httpx.Response) -> None:
    if response.status_code == 429 and cuota.endpoint(str(response.request.url)):
        await response.aread()
        cuota.limite_alcanzado(response.text)
