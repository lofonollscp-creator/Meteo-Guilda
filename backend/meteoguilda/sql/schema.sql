CREATE EXTENSION IF NOT EXISTS postgis;

-- Observaciones de balizas APRS (solo con posición)
CREATE TABLE IF NOT EXISTS obs (
  id           BIGSERIAL PRIMARY KEY,
  callsign     TEXT        NOT NULL,
  ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
  geom         GEOGRAPHY(Point, 4326) NOT NULL,
  wind_dir     REAL, wind_ms REAL, gust_ms REAL, temp_c REAL,
  rain_1h_mm   REAL, rain_24h_mm REAL, humidity REAL, pressure_hpa REAL
);
ALTER TABLE obs ALTER COLUMN ts SET DEFAULT clock_timestamp();
CREATE INDEX IF NOT EXISTS obs_geom_gix ON obs USING GIST (geom);
CREATE INDEX IF NOT EXISTS obs_call_ts  ON obs (callsign, ts DESC);
CREATE INDEX IF NOT EXISTS obs_ts       ON obs (ts DESC);

-- Repetidores de radioaficionado
CREATE TABLE IF NOT EXISTS repeaters (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  callsign    TEXT,
  band        TEXT,                -- 2m, 70cm, 10m, ...
  freq_mhz    DOUBLE PRECISION NOT NULL,   -- salida del repetidor (RX del usuario)
  shift_khz   INTEGER,             -- desplazamiento (p. ej. -600)
  tone_hz     REAL,                -- subtono CTCSS de acceso
  mode        TEXT DEFAULT 'FM',   -- FM, DMR, D-STAR, C4FM...
  height_m    REAL DEFAULT 10,     -- altura de antena sobre el suelo
  range_km    REAL DEFAULT 40,
  source      TEXT,
  geom        GEOGRAPHY(Point, 4326) NOT NULL,
  coverage    GEOGRAPHY(Polygon, 4326),
  coverage_at TIMESTAMPTZ,
  UNIQUE (callsign, freq_mhz)
);
CREATE INDEX IF NOT EXISTS rep_geom_gix ON repeaters USING GIST (geom);
CREATE INDEX IF NOT EXISTS rep_cov_gix  ON repeaters USING GIST (coverage);

-- Extensión de repetidores (BrandMeister / HearHam / manual)
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS network     TEXT;
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS color_code  SMALLINT;
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS dmr_id      INTEGER;
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS power_w     REAL;
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS city        TEXT;
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS website     TEXT;
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS last_seen   TIMESTAMPTZ;
ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ DEFAULT now();
CREATE INDEX IF NOT EXISTS rep_call ON repeaters (callsign);

-- Perfiles anónimos (clave secreta) para sincronizar favoritos y cuaderno entre dispositivos, API y MCP
CREATE TABLE IF NOT EXISTS profiles (
  key_hash   TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  settings   JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS qsos (
  id        BIGSERIAL PRIMARY KEY,
  key_hash  TEXT NOT NULL REFERENCES profiles(key_hash) ON DELETE CASCADE,
  call      TEXT NOT NULL,
  qso_at    TIMESTAMPTZ NOT NULL,
  band      TEXT, mode TEXT, freq_mhz DOUBLE PRECISION,
  rst_sent  TEXT, rst_rcvd TEXT, grid TEXT, name TEXT, notes TEXT,
  qsl_sent  BOOLEAN NOT NULL DEFAULT FALSE,
  qsl_rcvd  BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS qsos_key_at ON qsos (key_hash, qso_at DESC);
CREATE TABLE IF NOT EXISTS kv_cache (
  k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
