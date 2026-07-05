import React, { useEffect, useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet'
import { Plus, Minus } from 'lucide-react'
import L from 'leaflet'

// WMO weather code -> emoji (matches WeatherWidget's buckets), for the little
// forecast badge shown on each stop pin for the trip's day.
function wxEmoji(code) {
  if (code == null) return ''
  if ([95, 96, 99].includes(code)) return '⛈️'
  if ([71, 73, 75, 77, 85, 86].includes(code)) return '🌨️'
  if ([80, 81, 82].includes(code)) return '🌦️'
  if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67].includes(code)) return '🌧️'
  if ([45, 48].includes(code)) return '🌫️'
  if (code === 3) return '☁️'
  if (code === 2) return '⛅'
  if (code === 1 || code === 0) return '☀️'
  return ''
}

// ─── Tile layer definitions ───────────────────────────────────────────────────
function buildLayers(stadiaKey, owmKey) {
  const sk = stadiaKey ? `?api_key=${stadiaKey}` : ''
  return {
    base: [
      {
        id: 'osm', label: 'Street', emoji: '🗺️',
        url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
      },
      {
        id: 'dark', label: 'Dark', emoji: '🌙',
        url: `https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png${sk}`,
        attribution: '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 20,
      },
      {
        id: 'satellite', label: 'Satellite', emoji: '🛰️',
        url: `https://tiles.stadiamaps.com/tiles/alidade_satellite/{z}/{x}/{y}{r}.jpg${sk}`,
        attribution: '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://www.esri.com/">Esri</a>',
        maxZoom: 20,
      },
    ],
    weather: owmKey
      ? [
          { id: 'precipitation', label: 'Rain',   emoji: '🌧️', url: `https://tile.openweathermap.org/map/precipitation_new/{z}/{x}/{y}.png?appid=${owmKey}`, attribution: '&copy; OpenWeatherMap', opacity: 0.6 },
          { id: 'clouds',        label: 'Clouds', emoji: '☁️', url: `https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid=${owmKey}`,        attribution: '&copy; OpenWeatherMap', opacity: 0.5 },
          { id: 'wind',          label: 'Wind',   emoji: '💨', url: `https://tile.openweathermap.org/map/wind_new/{z}/{x}/{y}.png?appid=${owmKey}`,           attribution: '&copy; OpenWeatherMap', opacity: 0.6 },
        ]
      : [],
  }
}

// ─── Location pin marker (real teardrop pin shape) ───────────────────────────
// Sightseeing stops: red pin with the stop number. Meal stops: amber pin with
// a fork glyph, so food is visually distinct from sightseeing on the map.
function pinIcon(n, meal = false, wx = '') {
  const fill   = meal ? '#d97706' : '#dc2626'
  const inner  = meal
    ? `<text x="18" y="22.5" text-anchor="middle" font-size="11">🍴</text>`
    : `<text x="18" y="22" text-anchor="middle" font-size="10" font-weight="800"
         font-family="system-ui,-apple-system,sans-serif" fill="#dc2626">${n}</text>`
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 48" width="36" height="48">
      <path d="M18 0 C8.06 0 0 8.06 0 18 C0 31.5 18 48 18 48 C18 48 36 31.5 36 18 C36 8.06 27.94 0 18 0Z"
        fill="${fill}" filter="drop-shadow(0 2px 4px rgba(0,0,0,0.45))"/>
      <circle cx="18" cy="18" r="9" fill="rgba(255,255,255,0.95)"/>
      ${inner}
    </svg>`
  // The trip-day forecast badge sits at the pin's top-right corner.
  const badge = wx
    ? `<div style="position:absolute;top:-3px;right:-7px;width:19px;height:19px;border-radius:50%;
         background:#fff;box-shadow:0 1px 4px rgba(0,0,0,0.35);display:flex;align-items:center;
         justify-content:center;font-size:12px;line-height:1;">${wx}</div>`
    : ''
  return L.divIcon({
    className: '',
    html: `<div style="position:relative;width:36px;height:48px;">${svg}${badge}</div>`,
    iconSize:    [36, 48],
    iconAnchor:  [18, 48],
    popupAnchor: [0,  -50],
  })
}

const homePin = L.divIcon({
  className: '',
  html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22">
    <circle cx="12" cy="12" r="10" fill="#10b981" stroke="#fff" stroke-width="2.5"
      filter="drop-shadow(0 1px 3px rgba(0,0,0,0.4))"/>
    <circle cx="12" cy="12" r="4" fill="rgba(255,255,255,0.9)"/>
  </svg>`,
  iconSize:    [22, 22],
  iconAnchor:  [11, 11],
  popupAnchor: [0, -13],
})

// ─── Popup content ────────────────────────────────────────────────────────────
function StopPopup({ stop }) {
  // Show the pre-generated, polished summary. Fall back to a tidied first
  // review sentence only for a place that hasn't been summarised yet.
  const fallback = stop.insight
    ? stop.insight.split('[RAW_REVIEW_REPOSITORY]').pop()
        .replace(/"/g, '').split(/[.!?]/)[0].trim().slice(0, 150)
    : ''
  const summary = (stop.summary && stop.summary.trim()) || (fallback ? `${fallback}.` : '')
  const meal = stop.is_meal

  return (
    <div style={{ minWidth: '210px', maxWidth: '256px', padding: '2px' }}>
      <div style={{ fontWeight: 700, fontSize: '14.5px', color: '#1e293b', marginBottom: '6px' }}>
        {stop.name}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '7px', marginBottom: '8px', flexWrap: 'wrap' }}>
        <span style={{
          background: meal ? '#fef3c7' : '#fee2e2', color: meal ? '#b45309' : '#dc2626',
          fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '10px',
        }}>
          {stop.visit_starts} – {stop.visit_ends}
        </span>
        {stop.rating > 0 && (
          <span style={{ fontSize: '11.5px', color: '#f59e0b', fontWeight: 700 }}>
            ★ {Number(stop.rating).toFixed(1)}
          </span>
        )}
      </div>
      {summary && (
        <div style={{ fontSize: '12.5px', color: '#374151', lineHeight: '1.5' }}>
          {summary}
        </div>
      )}
      {stop.vibe && (
        <div style={{ fontSize: '11px', color: '#9ca3af', marginTop: '8px', borderTop: '1px solid #f3f4f6', paddingTop: '6px' }}>
          {stop.vibe.split(',').slice(0, 4).map(v => v.trim()).join(' · ')}
        </div>
      )}
    </div>
  )
}

// ─── Layer Switcher ───────────────────────────────────────────────────────────
// A clean, always-visible control: base map as a segmented toggle, weather
// overlays as small chips beneath. No dropdown, no big icon.
function LayerSwitcher({ layers, activeBaseId, onBaseChange, activeWeatherId, onWeatherChange, dark }) {
  const map = useMap()
  const bg      = dark ? 'rgba(15,23,42,0.90)' : 'rgba(255,255,255,0.96)'
  const text    = dark ? '#e2e8f0' : '#1e293b'
  const border  = dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)'
  const trackBg = dark ? 'rgba(255,255,255,0.06)' : '#f3f4f6'
  const activeBg = '#2563eb'

  const card = {
    background: bg, backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
    border: `1px solid ${border}`, borderRadius: '14px',
    boxShadow: '0 6px 24px rgba(0,0,0,0.18)', color: text,
  }
  const zoomBtn = {
    width: '38px', height: '34px', border: 'none', background: 'transparent', color: text,
    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
  }
  const seg = (active) => ({
    padding: '6px 13px', borderRadius: '8px', cursor: 'pointer', border: 'none',
    background: active ? activeBg : 'transparent',
    color: active ? '#fff' : text,
    fontSize: '12px', fontWeight: 700, whiteSpace: 'nowrap',
    transition: 'background .15s, color .15s',
  })
  const chip = (active) => ({
    padding: '5px 10px', borderRadius: '20px', cursor: 'pointer',
    border: `1px solid ${active ? activeBg : border}`,
    background: active ? (dark ? 'rgba(37,99,235,0.30)' : '#eff6ff') : 'transparent',
    color: active ? (dark ? '#bfdbfe' : '#2563eb') : text,
    fontSize: '11.5px', fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: '5px',
  })

  return (
    <div style={{
      position: 'absolute', bottom: '26px', right: '12px', zIndex: 1000,
      display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px',
    }}>
      {/* Zoom, moved off the map's top-left corner so it never overlaps the chat. */}
      <div style={{ ...card, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <button style={zoomBtn} onClick={() => map.zoomIn()} title="Zoom in"><Plus size={17} /></button>
        <div style={{ height: '1px', background: border }} />
        <button style={zoomBtn} onClick={() => map.zoomOut()} title="Zoom out"><Minus size={17} /></button>
      </div>

      {/* Base map + weather overlays. */}
      <div style={{ ...card, padding: '6px' }}>
        <div style={{ display: 'flex', gap: '2px', background: trackBg, borderRadius: '10px', padding: '3px' }}>
          {layers.base.map(l => (
            <button key={l.id} onClick={() => onBaseChange(l.id)} style={seg(activeBaseId === l.id)}>
              {l.label}
            </button>
          ))}
        </div>
        {layers.weather.length > 0 && (
          <div style={{ display: 'flex', gap: '6px', padding: '8px 3px 2px', flexWrap: 'wrap' }}>
            {layers.weather.map(l => (
              <button key={l.id}
                onClick={() => onWeatherChange(activeWeatherId === l.id ? null : l.id)}
                style={chip(activeWeatherId === l.id)}>
                <span>{l.emoji}</span>{l.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function FlyTo({ center }) {
  const map = useMap()
  useEffect(() => { if (center) map.flyTo(center, 13, { duration: 1.2 }) }, [center, map])
  return null
}

// ─── Main export ──────────────────────────────────────────────────────────────
export default function TripMap({ userLocation, stops, route, stadiaApiKey = '', owmApiKey = '', stopWeather = {} }) {
  const [activeBaseId, setActiveBaseId]       = useState('osm')
  const [activeWeatherId, setActiveWeatherId] = useState(null)

  const layers      = buildLayers(stadiaApiKey, owmApiKey)
  const baseLayer   = layers.base.find(l => l.id === activeBaseId) || layers.base[0]
  const weatherLayer = activeWeatherId ? layers.weather.find(l => l.id === activeWeatherId) : null
  const dark        = activeBaseId === 'dark'
  // Google Maps uses roughly #1a73e8 for its route line -- a highly-saturated
  // blue that reads clearly against any base map. On dark maps we lighten it
  // slightly so it still pops against the near-black background.
  const routeColor  = dark ? '#60b4ff' : '#1a6bef'
  const center      = userLocation || [8.5241, 76.9366]

  return (
    <MapContainer center={center} zoom={12} style={{ width: '100%', height: '100%' }} zoomControl={false}>
      <TileLayer key={baseLayer.id} url={baseLayer.url}
        attribution={baseLayer.attribution} maxZoom={baseLayer.maxZoom} />

      {weatherLayer && (
        <TileLayer key={weatherLayer.id} url={weatherLayer.url}
          attribution={weatherLayer.attribution} opacity={weatherLayer.opacity} />
      )}

      {userLocation && (
        <Marker position={userLocation} icon={homePin}>
          <Popup><span style={{ fontWeight: 600, fontSize: '13px' }}>You are here</span></Popup>
        </Marker>
      )}

      {stops.map((s, i) => (
        <Marker key={`${s.name}-${i}`} position={[s.lat, s.lng]}
          icon={pinIcon(i + 1, s.is_meal, wxEmoji(stopWeather[s.name]?.weather_code))}>
          <Popup><StopPopup stop={s} /></Popup>
        </Marker>
      ))}

      {route?.coordinates && (
        <Polyline
          positions={route.coordinates.map(([lng, lat]) => [lat, lng])}
          color={routeColor}
          weight={5}
          opacity={0.9}
        />
      )}

      {userLocation && <FlyTo center={userLocation} />}

      <LayerSwitcher
        layers={layers}
        activeBaseId={activeBaseId}
        onBaseChange={setActiveBaseId}
        activeWeatherId={activeWeatherId}
        onWeatherChange={setActiveWeatherId}
        dark={dark}
      />
    </MapContainer>
  )
}
