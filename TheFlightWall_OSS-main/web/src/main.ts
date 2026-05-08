import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./style.css";

type Flight = {
  icao24: string;
  callsign: string;
  origin_country: string;
  distance_km: number;
  bearing_deg: number;
  lat: number;
  lon: number;
  baro_altitude: number;
  velocity: number;
  heading: number;
  on_ground: boolean;
};

const STORAGE_KEY = "flightwall-web-v1";

function loadPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as { lat: number; lon: number; radiusKm: number };
  } catch {
    return null;
  }
}

function savePrefs(p: { lat: number; lon: number; radiusKm: number }) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
}

const elLat = document.querySelector("#lat") as HTMLInputElement;
const elLon = document.querySelector("#lon") as HTMLInputElement;
const elRadius = document.querySelector("#radius") as HTMLInputElement;
const elApply = document.querySelector("#apply") as HTMLButtonElement;
const elGeo = document.querySelector("#geolocate") as HTMLButtonElement;
const elStatus = document.querySelector("#status") as HTMLSpanElement;
const tbody = document.querySelector("#flights-table tbody") as HTMLTableSectionElement;

const defaults = { lat: 37.7749, lon: -122.4194, radiusKm: 10 };
const saved = loadPrefs();
const initial = saved ?? defaults;
elLat.value = String(initial.lat);
elLon.value = String(initial.lon);
elRadius.value = String(initial.radiusKm);

const map = L.map("map").setView([initial.lat, initial.lon], 11);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
}).addTo(map);

const centerMarker = L.circleMarker([initial.lat, initial.lon], {
  radius: 8,
  color: "#2563eb",
  fillColor: "#3b82f6",
  fillOpacity: 0.9,
}).addTo(map);
centerMarker.bindTooltip("Search center");

let radiusCircle = L.circle([initial.lat, initial.lon], {
  radius: initial.radiusKm * 1000,
  color: "#3b82f6",
  weight: 2,
  fillOpacity: 0.06,
}).addTo(map);

const flightLayer = L.layerGroup().addTo(map);
let flightMarkers: L.CircleMarker[] = [];

function readParams() {
  return {
    lat: Number(elLat.value),
    lon: Number(elLon.value),
    radiusKm: Number(elRadius.value),
  };
}

function setStatus(msg: string, kind: "ok" | "err" | "" = "") {
  elStatus.textContent = msg;
  elStatus.classList.remove("status--ok", "status--err");
  if (kind === "ok") elStatus.classList.add("status--ok");
  if (kind === "err") elStatus.classList.add("status--err");
}

async function refresh() {
  const { lat, lon, radiusKm } = readParams();
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(radiusKm)) {
    setStatus("Enter valid numbers for latitude, longitude, and radius.", "err");
    return;
  }

  setStatus("Loading…", "");
  elApply.disabled = true;

  try {
    const u = new URL("/api/flights", window.location.origin);
    u.searchParams.set("lat", String(lat));
    u.searchParams.set("lon", String(lon));
    u.searchParams.set("radiusKm", String(radiusKm));
    const res = await fetch(u.toString());
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(typeof body.error === "string" ? body.error : `HTTP ${res.status}`, "err");
      return;
    }

    const flights = (body.flights ?? []) as Flight[];
    savePrefs({ lat, lon, radiusKm });

    map.setView([lat, lon], map.getZoom());
    centerMarker.setLatLng([lat, lon]);
    radiusCircle.remove();
    radiusCircle = L.circle([lat, lon], {
      radius: radiusKm * 1000,
      color: "#3b82f6",
      weight: 2,
      fillOpacity: 0.06,
    }).addTo(map);

    flightLayer.clearLayers();
    flightMarkers = [];

    for (const f of flights) {
      const m = L.circleMarker([f.lat, f.lon], {
        radius: 5,
        color: "#0f172a",
        weight: 1,
        fillColor: f.on_ground ? "#94a3b8" : "#22c55e",
        fillOpacity: 0.85,
      });
      const cs = f.callsign || "(no callsign)";
      m.bindTooltip(
        `${cs} · ${f.distance_km.toFixed(1)} km @ ${Math.round(f.bearing_deg)}°`
      );
      m.addTo(flightLayer);
      flightMarkers.push(m);
    }

    tbody.replaceChildren();
    const frag = document.createDocumentFragment();
    for (const f of flights) {
      const tr = document.createElement("tr");
      const fmt = (n: number) => (Number.isFinite(n) ? String(Math.round(n)) : "—");
      tr.innerHTML = `
        <td>${f.callsign || "—"}</td>
        <td><code>${f.icao24}</code></td>
        <td>${f.origin_country || "—"}</td>
        <td>${f.distance_km.toFixed(1)}</td>
        <td>${f.bearing_deg.toFixed(0)}</td>
        <td>${fmt(f.baro_altitude)}</td>
        <td>${fmt(f.velocity)}</td>
        <td>${fmt(f.heading)}</td>
        <td>${f.on_ground ? "yes" : "no"}</td>
      `;
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);

    const t =
      body.fetchedAt != null
        ? ` Updated ${new Date(body.fetchedAt).toLocaleTimeString()}.`
        : "";
    setStatus(`${flights.length} flight(s) in radius.${t}`, "ok");
  } catch (e) {
    setStatus(e instanceof Error ? e.message : "Request failed", "err");
  } finally {
    elApply.disabled = false;
  }
}

elApply.addEventListener("click", () => void refresh());

elGeo.addEventListener("click", () => {
  if (!navigator.geolocation) {
    setStatus("Geolocation not available in this browser.", "err");
    return;
  }
  setStatus("Locating…", "");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      elLat.value = String(pos.coords.latitude);
      elLon.value = String(pos.coords.longitude);
      void refresh();
    },
    () => setStatus("Could not read your location (permission or timeout).", "err"),
    { enableHighAccuracy: true, timeout: 12_000 }
  );
});

void refresh();
setInterval(() => void refresh(), 45_000);
