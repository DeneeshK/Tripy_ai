import React, { useState, useEffect } from 'react'
import TripMap from './components/TripMap'
import ChatPanel from './components/ChatPanel'

const API = ''

export default function App() {
  const [userLocation, setUserLocation] = useState(null)
  const [stops, setStops]   = useState([])
  const [route, setRoute]   = useState(null)
  const [gpsStatus, setGpsStatus] = useState('requesting')

  // Ask for GPS on load
  useEffect(() => {
    if (!navigator.geolocation) {
      setGpsStatus('unavailable')
      return
    }
    navigator.geolocation.getCurrentPosition(
      pos => {
        setUserLocation([pos.coords.latitude, pos.coords.longitude])
        setGpsStatus('ok')
      },
      () => {
        // Fallback to Trivandrum city centre if denied
        setUserLocation([8.5241, 76.9366])
        setGpsStatus('denied')
      },
      { enableHighAccuracy: true, timeout: 8000 }
    )
  }, [])

  async function onPlanReady(plan) {
    setStops(plan.stops || [])

    // Fetch OSRM route polyline for the ordered stops
    if (plan.coords?.length > 1) {
      const coordStr = plan.coords.map(c => `${c[0]},${c[1]}`).join(';')
      try {
        const res  = await fetch(`${API}/api/route?coords=${encodeURIComponent(coordStr)}`)
        const geom = await res.json()
        setRoute(geom)
      } catch {
        setRoute(null)
      }
    }
  }

  const layout = {
    root: {
      display: 'flex', width: '100vw', height: '100vh', overflow: 'hidden',
    },
    left: {
      width: '380px', flexShrink: 0, display: 'flex', flexDirection: 'column',
      height: '100vh', overflow: 'hidden',
    },
    right: {
      flex: 1, position: 'relative',
    },
    gpsBadge: {
      position: 'absolute', top: '12px', left: '50%', transform: 'translateX(-50%)',
      zIndex: 1000, background: gpsStatus === 'denied' ? '#fef3c7' : '#d1fae5',
      color: gpsStatus === 'denied' ? '#92400e' : '#065f46',
      padding: '4px 12px', borderRadius: '20px', fontSize: '12px', fontWeight: 600,
      pointerEvents: 'none', boxShadow: '0 2px 8px rgba(0,0,0,.15)',
      display: gpsStatus === 'ok' ? 'none' : 'block',
    },
  }

  return (
    <div style={layout.root}>
      <div style={layout.left}>
        <ChatPanel userLocation={userLocation} onPlanReady={onPlanReady} />
      </div>
      <div style={layout.right}>
        <div style={layout.gpsBadge}>
          {gpsStatus === 'denied' && '📍 Using Trivandrum centre — location access was denied'}
          {gpsStatus === 'requesting' && '📍 Getting your location…'}
        </div>
        <TripMap userLocation={userLocation} stops={stops} route={route} />
      </div>
    </div>
  )
}
