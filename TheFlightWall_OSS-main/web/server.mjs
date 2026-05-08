/**
 * Small API proxy: OpenSky OAuth2 + states/all, matching firmware GeoUtils + OpenSkyFetcher.
 * Set OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET (same as firmware APIConfiguration.h).
 */
import express from "express";
import { readFileSync, existsSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadDotenv() {
  try {
    const raw = readFileSync(join(__dirname, ".env"), "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const i = t.indexOf("=");
      if (i <= 0) continue;
      const k = t.slice(0, i).trim();
      let v = t.slice(i + 1).trim();
      if (
        (v.startsWith('"') && v.endsWith('"')) ||
        (v.startsWith("'") && v.endsWith("'"))
      ) {
        v = v.slice(1, -1);
      }
      if (!process.env[k]) process.env[k] = v;
    }
  } catch {
    /* no .env */
  }
}

loadDotenv();

const OPENSKY_TOKEN_URL =
  process.env.OPENSKY_TOKEN_URL ??
  "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token";
const OPENSKY_BASE_URL =
  process.env.OPENSKY_BASE_URL ?? "https://opensky-network.org";

const kPi = Math.PI;

function degreesToRadians(deg) {
  return (deg * kPi) / 180;
}

function radiansToDegrees(rad) {
  return (rad * 180) / kPi;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dlat = degreesToRadians(lat2 - lat1);
  const dlon = degreesToRadians(lon2 - lon1);
  const a =
    Math.sin(dlat / 2) ** 2 +
    Math.cos(degreesToRadians(lat1)) *
      Math.cos(degreesToRadians(lat2)) *
      Math.sin(dlon / 2) ** 2;
  const c = 2 * Math.asin(Math.sqrt(a));
  return R * c;
}

function computeBearingDeg(lat1, lon1, lat2, lon2) {
  const dlon = degreesToRadians(lon2 - lon1);
  const lat1r = degreesToRadians(lat1);
  const lat2r = degreesToRadians(lat2);
  const x = Math.sin(dlon) * Math.cos(lat2r);
  const y =
    Math.cos(lat1r) * Math.sin(lat2r) -
    Math.sin(lat1r) * Math.cos(lat2r) * Math.cos(dlon);
  const initial = Math.atan2(x, y);
  return ((radiansToDegrees(initial) + 360) % 360);
}

function centeredBoundingBox(lat, lon, radiusKm) {
  const latDelta = radiusKm / 111;
  const lonDelta = radiusKm / (111 * Math.cos(degreesToRadians(lat)));
  return {
    latMin: lat - latDelta,
    latMax: lat + latDelta,
    lonMin: lon - lonDelta,
    lonMax: lon + lonDelta,
  };
}

function urlEncodeForm(value) {
  return encodeURIComponent(value).replace(/%20/g, "+");
}

let cachedToken = null;
let tokenExpiresAtMs = 0;

async function ensureToken(forceRefresh) {
  const id = process.env.OPENSKY_CLIENT_ID ?? "";
  const secret = process.env.OPENSKY_CLIENT_SECRET ?? "";
  if (!id || !secret) {
    const err = new Error(
      "OpenSky OAuth not configured. Set OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET in web/.env"
    );
    err.code = "NO_CREDS";
    throw err;
  }

  const safetySkewMs = 60_000;
  const now = Date.now();
  if (
    !forceRefresh &&
    cachedToken &&
    now + safetySkewMs < tokenExpiresAtMs
  ) {
    return cachedToken;
  }

  const body =
    "grant_type=client_credentials" +
    "&client_id=" +
    urlEncodeForm(id) +
    "&client_secret=" +
    urlEncodeForm(secret);

  const res = await fetch(OPENSKY_TOKEN_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Accept: "application/json",
    },
    body,
  });

  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`OpenSky token HTTP ${res.status}: ${text.slice(0, 200)}`);
    err.code = "TOKEN_FAIL";
    throw err;
  }

  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    const err = new Error("OpenSky token: invalid JSON");
    err.code = "TOKEN_PARSE";
    throw err;
  }

  const access = doc.access_token;
  const expiresIn = typeof doc.expires_in === "number" ? doc.expires_in : 1800;
  if (!access) {
    const err = new Error("OpenSky token: missing access_token");
    err.code = "TOKEN_MISSING";
    throw err;
  }

  cachedToken = access;
  tokenExpiresAtMs = now + expiresIn * 1000;
  return cachedToken;
}

function parseStateRow(a, centerLat, centerLon, radiusKm) {
  if (!Array.isArray(a) || a.length < 17) return null;

  const icao24 = a[0] != null ? String(a[0]) : "";
  let callsign = a[1] != null ? String(a[1]) : "";
  callsign = callsign.trim();
  const origin_country = a[2] != null ? String(a[2]) : "";
  const time_position = a[3] == null ? 0 : Number(a[3]);
  const last_contact = a[4] == null ? 0 : Number(a[4]);
  const lon = a[5] == null ? NaN : Number(a[5]);
  const lat = a[6] == null ? NaN : Number(a[6]);
  const baro_altitude = a[7] == null ? NaN : Number(a[7]);
  const on_ground = Boolean(a[8]);
  const velocity = a[9] == null ? NaN : Number(a[9]);
  const heading = a[10] == null ? NaN : Number(a[10]);
  const vertical_rate = a[11] == null ? NaN : Number(a[11]);
  const sensors = a[12] == null ? 0 : Number(a[12]);
  const geo_altitude = a[13] == null ? NaN : Number(a[13]);
  const squawk = a[14] != null ? String(a[14]) : "";
  const spi = Boolean(a[15]);
  const position_source = a[16] == null ? 0 : Number(a[16]);

  if (Number.isNaN(lat) || Number.isNaN(lon)) return null;

  const distance_km = haversineKm(centerLat, centerLon, lat, lon);
  if (distance_km > radiusKm) return null;
  const bearing_deg = computeBearingDeg(centerLat, centerLon, lat, lon);

  return {
    icao24,
    callsign,
    origin_country,
    time_position,
    last_contact,
    lon,
    lat,
    baro_altitude,
    on_ground,
    velocity,
    heading,
    vertical_rate,
    sensors,
    geo_altitude,
    squawk,
    spi,
    position_source,
    distance_km,
    bearing_deg,
  };
}

async function fetchStates(centerLat, centerLon, radiusKm) {
  const { latMin, latMax, lonMin, lonMax } = centeredBoundingBox(
    centerLat,
    centerLon,
    radiusKm
  );

  const url =
    `${OPENSKY_BASE_URL}/api/states/all?` +
    `lamin=${latMin.toFixed(6)}&lamax=${latMax.toFixed(6)}&` +
    `lomin=${lonMin.toFixed(6)}&lomax=${lonMax.toFixed(6)}`;

  let token = await ensureToken(false);
  let res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (res.status === 401) {
    token = await ensureToken(true);
    res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
    });
  }

  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`OpenSky states HTTP ${res.status}: ${text.slice(0, 200)}`);
    err.code = "STATES_FAIL";
    throw err;
  }

  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    const err = new Error("OpenSky states: invalid JSON");
    err.code = "STATES_PARSE";
    throw err;
  }

  const states = doc.states;
  if (!states || !Array.isArray(states)) return [];

  const out = [];
  for (const row of states) {
    const s = parseStateRow(row, centerLat, centerLon, radiusKm);
    if (s) out.push(s);
  }
  out.sort((a, b) => a.distance_km - b.distance_km);
  return out;
}

const app = express();

app.get("/api/health", (_req, res) => {
  const hasCreds = Boolean(
    process.env.OPENSKY_CLIENT_ID && process.env.OPENSKY_CLIENT_SECRET
  );
  res.json({ ok: true, openskyConfigured: hasCreds });
});

app.get("/api/flights", async (req, res) => {
  const lat = Number(req.query.lat);
  const lon = Number(req.query.lon);
  const radiusKm = Number(req.query.radiusKm ?? req.query.radius);

  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(radiusKm)) {
    res.status(400).json({
      error: "Query params lat, lon, and radiusKm (or radius) must be numbers.",
    });
    return;
  }
  if (radiusKm <= 0 || radiusKm > 250) {
    res.status(400).json({ error: "radiusKm must be between 0 and 250." });
    return;
  }

  try {
    const flights = await fetchStates(lat, lon, radiusKm);
    res.json({
      center: { lat, lon, radiusKm },
      count: flights.length,
      flights,
      fetchedAt: new Date().toISOString(),
    });
  } catch (e) {
    if (e.code === "NO_CREDS") {
      res.status(503).json({ error: e.message });
      return;
    }
    console.error(e);
    res.status(502).json({ error: e.message || "Upstream error" });
  }
});

const distDir = join(__dirname, "dist");
if (existsSync(distDir)) {
  app.use(express.static(distDir));
}

const port = Number(process.env.API_PORT ?? process.env.PORT ?? 3001);
app.listen(port, () => {
  console.log(`FlightWall API listening on http://127.0.0.1:${port}`);
});
