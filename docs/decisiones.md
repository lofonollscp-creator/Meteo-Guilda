# Decisiones técnicas

## Lenguaje del backend: Python (FastAPI)
El trabajo es de E/S (APIs, sockets) y de datos meteorológicos (GRIB, xarray, cfgrib), donde Python tiene el
ecosistema. Con 6 lugares la carga es mínima. Rust se reserva para un componente concreto si algún día hace falta
(p. ej. el ingestor APRS con miles de paquetes/s, o procesado de rejillas).

## Base de datos: PostgreSQL + extensión PostGIS
PostGIS no es una alternativa a PostgreSQL: es una extensión del mismo servidor. La pregunta real es
"¿PostgreSQL a secas o con PostGIS?". Decisión: **PostgreSQL con PostGIS** (imagen `postgis/postgis`),
migrando desde SQLite cuando empiece el planificador de rutas.

| | SQLite (hoy) | PostgreSQL | PostgreSQL + PostGIS |
|---|---|---|---|
| Despliegue | Fichero, cero mantenimiento | Servicio aparte | Igual que PostgreSQL (otra imagen) |
| Series temporales | Válido a pequeña escala | Bueno (índices por tiempo, particionado) | Igual, y opcional TimescaleDB |
| Estaciones cercanas | Haversine en Python | Haversine o `earthdistance` | `ST_DWithin` con índice GiST (rápido y exacto) |
| Rutas (GPX, carretera) | Manual | Manual | `ST_LineInterpolatePoint`, `ST_Buffer`, intersecciones, muestreo cada N km |
| Escritura concurrente | Limitada (un escritor) | Excelente | Excelente |
| Coste | 0 | +~150 MB RAM | +~150 MB RAM |

Razón: el valor diferencial del proyecto es cruzar la previsión con rutas y zonas (monte, carretera, mar);
eso son consultas espaciales y PostGIS las resuelve en SQL con índices. El coste frente a PostgreSQL a secas es
prácticamente nulo.
