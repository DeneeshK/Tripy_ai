import React, { useEffect, useRef, useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet'
import L from 'leaflet'

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
function pinIcon(n, meal = false) {
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
  return L.divIcon({
    className: '',
    html: svg,
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
  // Pull first real sentence from insight (the full visitor review text)
  const brief = stop.insight
    ? stop.insight.split(/[.!?]/)[0].replace(/^[^:]*:\s*/, '').trim().slice(0, 130)
    : ''

  return (
    <div style={{ minWidth: '180px', maxWidth: '240px', padding: '2px' }}>
      <div style={{ fontWeight: 700, fontSize: '14px', color: '#1e293b', marginBottom: '4px' }}>
        {stop.name}
      </div>
      <div style={{
        display: 'inline-block', background: '#fee2e2', color: '#dc2626',
        fontSize: '11px', fontWeight: 600, padding: '2px 7px', borderRadius: '10px',
        marginBottom: '6px',
      }}>
        {stop.visit_starts} – {stop.visit_ends}
      </div>
      {stop.vibe && (
        <div style={{ fontSize: '11px', color: '#6b7280', marginBottom: '5px' }}>
          {stop.vibe.split(',').slice(0, 3).map(v => v.trim()).join(' · ')}
        </div>
      )}
      {brief && (
        <div style={{ fontSize: '12px', color: '#374151', lineHeight: '1.4', borderTop: '1px solid #f3f4f6', paddingTop: '5px' }}>
          {brief}{brief.length === 130 ? '…' : '.'}
        </div>
      )}
    </div>
  )
}

// ─── Layer Switcher ───────────────────────────────────────────────────────────
function LayerSwitcher({ layers, activeBaseId, onBaseChange, activeWeatherId, onWeatherChange, dark }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const bg     = dark ? 'rgba(15,23,42,0.92)' : 'rgba(255,255,255,0.95)'
  const text   = dark ? '#e2e8f0' : '#1e293b'
  const border = dark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)'
  const sub    = dark ? '#94a3b8' : '#6b7280'
  const activeBg  = dark ? 'rgba(37,99,235,0.25)' : '#eff6ff'
  const activeBdr = '#2563eb'
  const hoverBg   = dark ? 'rgba(255,255,255,0.06)' : '#f8fafc'

  const cardStyle = {
    background: bg,
    backdropFilter: 'blur(12px)',
    border: `1px solid ${border}`,
    borderRadius: '14px',
    padding: '14px',
    boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
    minWidth: '220px',
    color: text,
  }

  const sectionLabel = {
    fontSize: '10px', fontWeight: 700, color: sub,
    textTransform: 'uppercase', letterSpacing: '0.08em',
    marginBottom: '8px',
  }

  const optionBtn = (active) => ({
    flex: 1, padding: '9px 6px', borderRadius: '9px', cursor: 'pointer',
    border: `1.5px solid ${active ? activeBdr : border}`,
    background: active ? activeBg : 'transparent',
    color: text, fontSize: '12px', fontWeight: 600,
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px',
    transition: 'background 0.15s, border-color 0.15s',
  })

  return (
    <div ref={ref} style={{ position: 'absolute', bottom: '32px', right: '12px', zIndex: 1000, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px' }}>
      {open && (
        <div style={cardStyle}>
          <p style={sectionLabel}>Map style</p>
          <div style={{ display: 'flex', gap: '7px', marginBottom: layers.weather.length ? '14px' : 0 }}>
            {layers.base.map(l => (
              <button key={l.id} onClick={() => onBaseChange(l.id)} style={optionBtn(activeBaseId === l.id)}>
                <span style={{ fontSize: '22px' }}>{l.emoji}</span>
                {l.label}
              </button>
            ))}
          </div>
          {layers.weather.length > 0 && (
            <>
              <p style={sectionLabel}>Weather overlay</p>
              <div style={{ display: 'flex', gap: '7px' }}>
                {layers.weather.map(l => (
                  <button key={l.id}
                    onClick={() => onWeatherChange(activeWeatherId === l.id ? null : l.id)}
                    style={optionBtn(activeWeatherId === l.id)}>
                    <span style={{ fontSize: '20px' }}>{l.emoji}</span>
                    {l.label}
                  </button>
                ))}
              </div>
            </>
          )}
          {layers.weather.length === 0 && (
            <p style={{ fontSize: '11px', color: sub, marginTop: '10px', borderTop: `1px solid ${border}`, paddingTop: '10px' }}>
              Add <code>OWM_API_KEY</code> to <code>.env</code> for weather overlays.
            </p>
          )}
        </div>
      )}

      <button onClick={() => setOpen(v => !v)} style={{
        background: bg, backdropFilter: 'blur(12px)',
        border: `1px solid ${border}`, borderRadius: '10px',
        padding: '8px 14px', cursor: 'pointer',
        boxShadow: '0 2px 10px rgba(0,0,0,0.15)',
        display: 'flex', alignItems: 'center', gap: '7px',
        color: text, fontSize: '13px', fontWeight: 600,
        transition: 'box-shadow 0.15s',
      }}>
        <span style={{ fontSize: '18px' }}>
          {layers.base.find(l => l.id === activeBaseId)?.emoji || '🗺️'}
        </span>
        Layers
        <span style={{ fontSize: '10px', opacity: 0.6 }}>{open ? '▲' : '▼'}</span>
      </button>
    </div>
  )
}

function FlyTo({ center }) {
  const map = useMap()
  useEffect(() => { if (center) map.flyTo(center, 13, { duration: 1.2 }) }, [center, map])
  return null
}

// ─── Main export ──────────────────────────────────────────────────────────────
export default function TripMap({ userLocation, stops, route, stadiaApiKey = '', owmApiKey = '' }) {
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
    <MapContainer center={center} zoom={12} style={{ width: '100%', height: '100%' }} zoomControl>
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
        <Marker key={`${s.name}-${i}`} position={[s.lat, s.lng]} icon={pinIcon(i + 1, s.is_meal)}>
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
