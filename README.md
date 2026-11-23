# Meteo Guilda · Radio Guilda

Previsión meteorológica a partir de datos abiertos (ECMWF, ICON, GFS, Météo-France vía Open-Meteo) y balizas meteo
APRS, con lectura de IA (OpenRouter, modelos gratuitos), **planificador de rutas** con la meteo a la hora de paso,
**espacio para radioaficionados** y un **servidor MCP** para conectar otras IAs.
Plan: [PLAN.md](PLAN.md) · [despliegue en VPS compartido](docs/DEPLOY.md) · [API](docs/API.md) · [decisiones](docs/decisiones.md).

## Qué incluye (v0.4)
**Meteo Guilda**
- Inicio con el resumen del día, 6 lugares principales (Garòs, Premià de Mar, Tórtoles de Esgueva, Bresca, La Garrovilla,
  Bagnères-de-Bigorre; coordenadas verificadas con dos fuentes), búsqueda libre, favoritos y anclados.
- Meteograma, gráficos (temperatura con banda entre modelos, lluvia, nieve, viento con flechas, nubes, presión, isocero),
  rosa de los vientos, Sol y Luna y balizas APRS cercanas.
- Rutas a pie/en coche o GPX: meteo a la hora de paso, semáforo de riesgo, perfil coloreado, mejores ventanas, exportación GPX.

**Radio Guilda**
- Repetidores FM/DMR/D-STAR/C4FM (BrandMeister + HearHam) en mapa con agrupación, ficha, cobertura estimada, exportación a CHIRP.
- Propagación troposférica y HF, línea gris; pasos de satélites con trayectoria y calendario; ventanas para operar en portátil.
- Cuaderno QSL: duplicados, ADIF (importar/exportar), estadísticas, cuadros trabajados, tarjetas QSL en PNG.
- Herramientas: locator, distancia, longitud de antena, Morse con audio, código Q, fonético, CTCSS, dBm.

**Plataforma**
- Webapp instalable (PWA) con modo sin conexión; iconos y marca propios; tema claro/oscuro.
- **Ajustes de la cuenta** (IA sí/no, proveedor y modelo, claves propias cifradas) y **asistente de IA** con datos reales del lugar (docs/DEPLOY.md §12).
- 8 modelos para contrastar y observaciones de APRS, METAR y XEMA (§13); la lectura de IA llega aparte y no bloquea la página.
- Todo por API (`/api/openapi.json`, ver [docs/API.md](docs/API.md)) y por MCP (24 herramientas).
- Sincronización opcional con clave secreta (favoritos, QTH y cuaderno entre dispositivos, API y MCP).

## Desarrollo
```bash
# PostgreSQL con PostGIS (p. ej. con Docker)
docker run -d --name mg-db -e POSTGRES_USER=mg -e POSTGRES_PASSWORD=mg -e POSTGRES_DB=meteoguilda_test -p 5432:5432 postgis/postgis:16-3.4
cd backend && pip install -e '.[dev]'
MG_DATABASE_URL=postgresql://mg:mg@127.0.0.1:5432/meteoguilda_test python -m pytest   # los tests de BD se omiten si no hay PostGIS
```
Producción: `./desplegar_produccion.sh` (ver [docs/DEPLOY.md](docs/DEPLOY.md), §9). Acceso: toda la web exige iniciar sesión con la cuenta de Guilda Work (§10); MCP: `https://radio.guildawork.com/mcp` con ese mismo login (§11).
