import React, { useState, useEffect, useCallback, useRef } from 'react'
import TripMap from './components/TripMap'
import ChatPanel from './components/ChatPanel'

const API = ''
const POLL_INTERVAL_MS = 30 * 60 * 1000  // 30 minutes

export default function App() {
  const [userLocation, setUserLocation] = useState(null)
  const [stops, setStops]   = useState([])
  const [route, setRoute]   = useState(null)
  const [gpsStatus, setGpsStatus] = useState('requesting')
  const [tripId, setTripId] = useState(null)
  const [weatherWarnings, setWeatherWarnings] = useState([])
  const [replanLoading, setReplanLoading] = useState(false)
  const [weatherDismissed, setWeatherDismissed] = useState(false)
  const [mapConfig, setMapConfig] = useState({ owm_api_key: '', stadia_api_key: '' })
  const pollRef = useRef(null)

  // Fetch map API keys from backend once on load
  useEffect(() => {
    fetch(`${API}/api/config`)
      .then(r => r.ok ? r.json() : {})
      .then(cfg => setMapConfig(cfg))
      .catch(() => {})
  }, [])

  // Ask for GPS on load
  useEffect(() => {
    if (!navigator.geolocation) { setGpsStatus('unavailable'); return }
    navigator.geolocation.getCurrentPosition(
      pos => { setUserLocation([pos.coords.latitude, pos.coords.longitude]); setGpsStatus('ok') },
      ()  => { setUserLocation([8.5241, 76.9366]); setGpsStatus('denied') },
      { enableHighAccuracy: true, timeout: 8000 }
    )
  }, [])

  const fetchRoute = useCallback(async (planCoords, homeCoords) => {
    const all = homeCoords ? [homeCoords, ...planCoords] : planCoords
    if (all.length < 2) return
    try {
      const coordStr = all.map(c => `${c[0]},${c[1]}`).join(';')
      const res  = await fetch(`${API}/api/route?coords=${encodeURIComponent(coordStr)}`)
      if (res.ok) setRoute(await res.json())
    } catch { setRoute(null) }
  }, [])

  const applyPlan = useCallback((plan, homeCoords) => {
    setStops(plan.stops || [])
    setWeatherDismissed(false)
    const stopCoords = (plan.stops || []).map(s => [s.lat, s.lng])
    fetchRoute(stopCoords, homeCoords || userLocation)
  }, [userLocation, fetchRoute])

  const onPlanReady = useCallback((plan) => {
    applyPlan(plan, userLocation)
    if (plan.trip_id) setTripId(plan.trip_id)
  }, [applyPlan, userLocation])

  // Weather polling -- runs every 30 min while a trip is active
  const pollWeather = useCallback(async () => {
    if (!tripId) return
    try {
      const res = await fetch(`${API}/api/trip/${tripId}/check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_lat: userLocation?.[0],
          current_lng: userLocation?.[1],
          auto_replan: false,
        }),
      })
      if (!res.ok) return
      const data = await res.json()
      if (data.needs_replan && data.weather_warnings?.length) {
        setWeatherWarnings(data.weather_warnings)
        setWeatherDismissed(false)
      }
    } catch { /* silently ignore transient network failures */ }
  }, [tripId, userLocation])

  useEffect(() => {
    if (!tripId) return
    pollWeather()  // immediate check when trip registers
    pollRef.current = setInterval(pollWeather, POLL_INTERVAL_MS)
    return () => clearInterval(pollRef.current)
  }, [tripId, pollWeather])

  const handleReplan = async () => {
    if (!tripId) return
    setReplanLoading(true)
    try {
      const res = await fetch(`${API}/api/trip/${tripId}/replan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_lat: userLocation?.[0],
          current_lng: userLocation?.[1],
          prefer_indoor: true,
        }),
      })
      if (res.ok) {
        const plan = await res.json()
        applyPlan(plan, userLocation)
        setWeatherWarnings([])
      }
    } catch (e) {
      console.error('Replan failed', e)
    } finally {
      setReplanLoading(false)
    }
  }

  const showWarning = weatherWarnings.length > 0 && !weatherDismissed

  const styles = {
    root: { display: 'flex', width: '100vw', height: '100vh', overflow: 'hidden' },
    left: { width: '380px', flexShrink: 0, display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' },
    right: { flex: 1, position: 'relative' },
    gpsBadge: {
      position: 'absolute', top: showWarning ? '76px' : '12px', left: '50%', transform: 'translateX(-50%)',
      zIndex: 1000, background: gpsStatus === 'denied' ? '#fef3c7' : '#d1fae5',
      color: gpsStatus === 'denied' ? '#92400e' : '#065f46',
      padding: '4px 12px', borderRadius: '20px', fontSize: '12px', fontWeight: 600,
      pointerEvents: 'none', boxShadow: '0 2px 8px rgba(0,0,0,.15)',
      display: gpsStatus === 'ok' ? 'none' : 'block', transition: 'top 0.2s',
    },
    weatherBanner: {
      position: 'absolute', top: '12px', left: '50%', transform: 'translateX(-50%)',
      zIndex: 1001, background: '#fef3c7', border: '1px solid #f59e0b',
      borderRadius: '10px', padding: '10px 16px', fontSize: '13px',
      color: '#78350f', boxShadow: '0 4px 12px rgba(0,0,0,.2)',
      display: 'flex', alignItems: 'center', gap: '12px', maxWidth: '480px', width: 'calc(100% - 80px)',
    },
  }

  return (
    <div style={styles.root}>
      <div style={styles.left}>
        <ChatPanel userLocation={userLocation} onPlanReady={onPlanReady} />
      </div>
      <div style={styles.right}>
        {showWarning && (
          <div style={styles.weatherBanner}>
            <span>⛈️</span>
            <span style={{ flex: 1 }}>
              <strong>Weather alert:</strong>{' '}
              {weatherWarnings[0].description} expected around{' '}
              {weatherWarnings[0].arrival_time} at {weatherWarnings[0].stop_name}.
              {weatherWarnings.length > 1 && ` (+${weatherWarnings.length - 1} more stops affected)`}
            </span>
            <button
              onClick={handleReplan}
              disabled={replanLoading}
              style={{ background: '#d97706', color: '#fff', border: 'none', borderRadius: '6px', padding: '5px 10px', cursor: 'pointer', fontSize: '12px', fontWeight: 600 }}>
              {replanLoading ? '…' : 'Replan'}
            </button>
            <button onClick={() => setWeatherDismissed(true)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: '#92400e', lineHeight: 1 }}>×</button>
          </div>
        )}
        <div style={styles.gpsBadge}>
          {gpsStatus === 'denied' && '📍 Using Trivandrum centre — location access was denied'}
          {gpsStatus === 'requesting' && '📍 Getting your location…'}
        </div>
        <TripMap
          userLocation={userLocation}
          stops={stops}
          route={route}
          stadiaApiKey={mapConfig.stadia_api_key || ''}
          owmApiKey={mapConfig.owm_api_key || ''}
        />
      </div>
    </div>
  )
}
