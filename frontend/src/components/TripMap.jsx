import React, { useEffect, useRef } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet'
import L from 'leaflet'

// Numbered circle marker for each stop
function numberedIcon(n, color = '#2563eb') {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:32px;height:32px;border-radius:50%;background:${color};
      color:#fff;font-weight:700;font-size:14px;
      display:flex;align-items:center;justify-content:center;
      border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.35)">
      ${n}
    </div>`,
    iconSize:   [32, 32],
    iconAnchor: [16, 16],
    popupAnchor:[0, -18],
  })
}

const homeIcon = L.divIcon({
  className: '',
  html: `<div style="
    width:18px;height:18px;border-radius:50%;background:#10b981;
    border:3px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.4)">
  </div>`,
  iconSize:   [18, 18],
  iconAnchor: [9, 9],
})

function FlyTo({ center }) {
  const map = useMap()
  useEffect(() => {
    if (center) map.flyTo(center, 13, { duration: 1.2 })
  }, [center, map])
  return null
}

export default function TripMap({ userLocation, stops, route }) {
  const center = userLocation || [8.5241, 76.9366]  // Trivandrum default

  return (
    <MapContainer
      center={center}
      zoom={12}
      style={{ width: '100%', height: '100%' }}
      zoomControl={true}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {userLocation && (
        <Marker position={userLocation} icon={homeIcon}>
          <Popup>You are here</Popup>
        </Marker>
      )}

      {stops.map((stop, i) => (
        <Marker key={stop.name} position={[stop.lat, stop.lng]} icon={numberedIcon(i + 1)}>
          <Popup>
            <strong>{stop.name}</strong><br />
            {stop.visit_starts} – {stop.visit_ends}<br />
            <span style={{ color: '#6b7280', fontSize: '12px' }}>{stop.vibe}</span>
          </Popup>
        </Marker>
      ))}

      {route && route.coordinates && (
        <Polyline
          positions={route.coordinates.map(([lng, lat]) => [lat, lng])}
          color="#2563eb"
          weight={4}
          opacity={0.7}
        />
      )}

      {userLocation && <FlyTo center={userLocation} />}
    </MapContainer>
  )
}
