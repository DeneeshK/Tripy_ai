import React, { useEffect, useRef, useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet'
import L from 'leaflet'

// ─── Tile layer definitions ───────────────────────────────────────────────────
// All URLs verified directly from provider docs, not from memory.
// Stadia: works on localhost with no key; production needs domain auth
//   (free, just whitelist your domain at client.stadiamaps.com) or STADIA_API_KEY.
// OWM: needs a free API key from openweathermap.org.

function buildLayers(stadiaKey, owmKey) {
  const sk = stadiaKey ? `?api_key=${stadiaKey}` : ''
  return {
    base: [
      {
        id: 'osm',
        label: 'Street',
        emoji: '🗺️',
        url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
        ext: 'png',
      },
      {
        id: 'dark',
        label: 'Dark',
        emoji: '🌙',
        url: `https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png${sk}`,
        attribution: '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 20,
        ext: 'png',
      },
      {
        id: 'satellite',
        label: 'Satellite',
        emoji: '🛰️',
        url: `https://tiles.stadiamaps.com/tiles/alidade_satellite/{z}/{x}/{y}{r}.jpg${sk}`,
        attribution: '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://www.esri.com/">Esri</a>',
        maxZoom: 20,
        ext: 'jpg',
      },
    ],
    weather: owmKey
      ? [
          {
            id: 'precipitation',
            label: 'Rain',
            emoji: '🌧️',
            url: `https://tile.openweathermap.org/map/precipitation_new/{z}/{x}/{y}.png?appid=${owmKey}`,
            attribution: '&copy; <a href="https://openweathermap.org">OpenWeatherMap</a>',
            opacity: 0.6,
          },
          {
            id: 'clouds',
            label: 'Clouds',
            emoji: '☁️',
            url: `https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid=${owmKey}`,
            attribution: '&copy; <a href="https://openweathermap.org">OpenWeatherMap</a>',
            opacity: 0.5,
          },
          {
            id: 'wind',
            label: 'Wind',
            emoji: '💨',
            url: `https://tile.openweathermap.org/map/wind_new/{z}/{x}/{y}.png?appid=${owmKey}`,
            attribution: '&copy; <a href="https://openweathermap.org">OpenWeatherMap</a>',
            opacity: 0.6,
          },
        ]
      : [],
  }
}

// ─── Marker helpers ───────────────────────────────────────────────────────────
function numberedIcon(n, dark = false) {
  const bg = dark ? '#60a5fa' : '#2563eb'
  return L.divIcon({
    className: '',
    html: `<div style="width:32px;height:32px;border-radius:50%;background:${bg};
      color:#fff;font-weight:700;font-size:14px;display:flex;align-items:center;
      justify-content:center;border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.45)">${n}</div>`,
    iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -18],
  })
}

const homeIcon = L.divIcon({
  className: '',
  html: `<div style="width:18px;height:18px;border-radius:50%;background:#10b981;
    border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4)"></div>`,
  iconSize: [18, 18], iconAnchor: [9, 9],
})

function FlyTo({ center }) {
  const map = useMap()
  useEffect(() => { if (center) map.flyTo(center, 13, { duration: 1.2 }) }, [center, map])
  return null
}

// ─── Layer Switcher (Google-Maps style, bottom-right) ─────────────────────────
function LayerSwitcher({ layers, activeBaseId, onBaseChange, activeWeatherId, onWeatherChange, dark }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  // Close on outside click
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const cardBg = dark ? '#1e293b' : '#fff'
  const text   = dark ? '#e2e8f0' : '#1e293b'
  const border = dark ? '#334155' : '#e2e8f0'
  const activeBg = dark ? '#2563eb22' : '#eff6ff'
  const activeBorder = '#2563eb'

  return (
    <div ref={ref} style={{
      position: 'absolute', bottom: '28px', right: '12px', zIndex: 1000,
      display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '8px',
    }}>
      {open && (
        <div style={{
          background: cardBg, border: `1px solid ${border}`, borderRadius: '12px',
          padding: '12px', boxShadow: '0 4px 20px rgba(0,0,0,0.2)',
          minWidth: '200px',
        }}>
          <div style={{ fontSize: '11px', fontWeight: 700, color: dark ? '#94a3b8' : '#6b7280',
            textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
            Map style
          </div>
          <div style={{ display: 'flex', gap: '8px', marginBottom: '14px' }}>
            {layers.base.map(l => (
              <button key={l.id} onClick={() => { onBaseChange(l.id); }}
                style={{
                  flex: 1, padding: '8px 4px', borderRadius: '8px', cursor: 'pointer',
                  border: `2px solid ${activeBaseId === l.id ? activeBorder : border}`,
                  background: activeBaseId === l.id ? activeBg : 'transparent',
                  color: text, fontSize: '12px', fontWeight: 600,
                  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px',
                }}>
                <span style={{ fontSize: '20px' }}>{l.emoji}</span>
                {l.label}
              </button>
            ))}
          </div>

          {layers.weather.length > 0 && (
            <>
              <div style={{ fontSize: '11px', fontWeight: 700, color: dark ? '#94a3b8' : '#6b7280',
                textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
                Weather overlay
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                {layers.weather.map(l => (
                  <button key={l.id}
                    onClick={() => onWeatherChange(activeWeatherId === l.id ? null : l.id)}
                    style={{
                      flex: 1, padding: '6px 4px', borderRadius: '8px', cursor: 'pointer',
                      border: `2px solid ${activeWeatherId === l.id ? activeBorder : border}`,
                      background: activeWeatherId === l.id ? activeBg : 'transparent',
                      color: text, fontSize: '12px', fontWeight: 600,
                      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px',
                    }}>
                    <span style={{ fontSize: '18px' }}>{l.emoji}</span>
                    {l.label}
                  </button>
                ))}
              </div>
            </>
          )}

          {layers.weather.length === 0 && (
            <div style={{ fontSize: '12px', color: dark ? '#64748b' : '#9ca3af', marginTop: '4px' }}>
              Add <code>OWM_API_KEY</code> to <code>.env</code> to enable weather overlays.
            </div>
          )}
        </div>
      )}

      <button onClick={() => setOpen(v => !v)} style={{
        background: cardBg, border: `1px solid ${border}`, borderRadius: '8px',
        padding: '8px 12px', cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,.15)',
        display: 'flex', alignItems: 'center', gap: '6px',
        color: text, fontSize: '13px', fontWeight: 600,
      }}>
        <span style={{ fontSize: '18px' }}>
          {layers.base.find(l => l.id === activeBaseId)?.emoji || '🗺️'}
        </span>
        Layers
      </button>
    </div>
  )
}

// ─── Main map component ───────────────────────────────────────────────────────
export default function TripMap({ userLocation, stops, route, stadiaApiKey = '', owmApiKey = '' }) {
  const [activeBaseId, setActiveBaseId] = useState('osm')
  const [activeWeatherId, setActiveWeatherId] = useState(null)

  const layers = buildLayers(stadiaApiKey, owmApiKey)
  const baseLayer = layers.base.find(l => l.id === activeBaseId) || layers.base[0]
  const weatherLayer = activeWeatherId ? layers.weather.find(l => l.id === activeWeatherId) : null
  const dark = activeBaseId === 'dark'

  const routeColor = dark ? '#60a5fa' : '#2563eb'
  const center = userLocation || [8.5241, 76.9366]

  return (
    <MapContainer center={center} zoom={12}
      style={{ width: '100%', height: '100%' }} zoomControl={true}>

      {/* Base layer -- key prop forces re-mount when URL changes */}
      <TileLayer key={baseLayer.id}
        url={baseLayer.url}
        attribution={baseLayer.attribution}
        maxZoom={baseLayer.maxZoom}
      />

      {/* Weather overlay -- togglable on top */}
      {weatherLayer && (
        <TileLayer key={weatherLayer.id}
          url={weatherLayer.url}
          attribution={weatherLayer.attribution}
          opacity={weatherLayer.opacity}
        />
      )}

      {userLocation && (
        <Marker position={userLocation} icon={homeIcon}>
          <Popup>You are here</Popup>
        </Marker>
      )}

      {stops.map((s, i) => (
        <Marker key={s.name} position={[s.lat, s.lng]} icon={numberedIcon(i + 1, dark)}>
          <Popup>
            <strong>{s.name}</strong><br />
            {s.visit_starts} – {s.visit_ends}<br />
            <span style={{ color: '#6b7280', fontSize: '12px' }}>{s.vibe?.split(',')[0]}</span>
          </Popup>
        </Marker>
      ))}

      {route?.coordinates && (
        <Polyline
          positions={route.coordinates.map(([lng, lat]) => [lat, lng])}
          color={routeColor} weight={4} opacity={0.75}
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
