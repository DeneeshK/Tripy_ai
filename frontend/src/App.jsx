import React, { useState, useEffect, useCallback, useRef } from 'react'
import TripMap from './components/TripMap'
import ChatPanel from './components/ChatPanel'
import WeatherWidget from './components/WeatherWidget'

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

  const isThunderstorm = weatherWarnings.some(w => w.is_thunderstorm)
  const severity       = isThunderstorm ? 'SEVERE' : 'WARNING'
  const sevColor       = isThunderstorm ? '#dc2626' : '#f59e0b'

  function weatherEmoji(w) {
    if (w.is_thunderstorm) return '⛈️'
    if (w.precipitation_probability >= 80) return '🌧️'
    if (w.precipitation_probability >= 60) return '🌦️'
    return '🌂'
  }

  const styles = {
    root: { position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden' },
    left: {
      position: 'absolute', top: '16px', left: '16px', bottom: '16px', width: '380px',
      display: 'flex', flexDirection: 'column', overflow: 'hidden', zIndex: 500,
      borderRadius: '16px', boxShadow: '0 10px 40px rgba(0,0,0,0.35)',
    },
    right: { position: 'absolute', inset: 0 },

    weatherCard: {
      position: 'absolute', top: '12px', left: '50%', transform: 'translateX(-50%)',
      zIndex: 1001,
      background: 'rgba(10, 15, 28, 0.93)',
      backdropFilter: 'blur(16px)',
      WebkitBackdropFilter: 'blur(16px)',
      border: `1px solid ${sevColor}44`,
      borderRadius: '16px',
      padding: '14px 16px',
      color: '#f1f5f9',
      boxShadow: `0 8px 40px rgba(0,0,0,0.5), 0 0 0 1px ${sevColor}22`,
      maxWidth: '440px',
      width: 'calc(100% - 80px)',
    },
    cardHeader: {
      display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px',
    },
    sevBadge: {
      background: sevColor, color: '#fff',
      fontSize: '10px', fontWeight: 800, letterSpacing: '0.06em',
      padding: '3px 8px', borderRadius: '20px',
      marginLeft: 'auto', flexShrink: 0,
    },
    stopRow: {
      background: 'rgba(255,255,255,0.06)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: '10px', padding: '9px 11px', marginBottom: '6px',
    },
    stopRowTop: { display: 'flex', alignItems: 'center', gap: '7px' },
    stopName:   { fontWeight: 700, fontSize: '13px', flex: 1 },
    stopTime:   { fontSize: '12px', color: '#94a3b8', flexShrink: 0 },
    stopDetail: { fontSize: '11.5px', color: '#94a3b8', marginTop: '3px', display: 'flex', alignItems: 'center', gap: '6px' },
    probPill:   {
      background: 'rgba(255,255,255,0.1)', borderRadius: '10px',
      padding: '1px 7px', fontSize: '11px', fontWeight: 600, color: '#cbd5e1',
    },
    actions:    { display: 'flex', gap: '8px', marginTop: '12px' },
    replanBtn:  {
      flex: 1, background: '#2563eb', color: '#fff', border: 'none',
      borderRadius: '9px', padding: '9px 12px', cursor: 'pointer',
      fontWeight: 700, fontSize: '13px', display: 'flex', alignItems: 'center',
      justifyContent: 'center', gap: '6px',
    },
    dismissBtn: {
      background: 'rgba(255,255,255,0.07)', color: '#94a3b8', border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: '9px', padding: '9px 14px', cursor: 'pointer', fontSize: '13px',
    },
    gpsBadge: {
      position: 'absolute',
      top: showWarning ? `${14 + 52 + weatherWarnings.length * 68 + 52}px` : '12px',
      left: '50%', transform: 'translateX(-50%)',
      zIndex: 1000,
      background: gpsStatus === 'denied' ? '#fef3c7' : '#d1fae5',
      color: gpsStatus === 'denied' ? '#92400e' : '#065f46',
      padding: '4px 12px', borderRadius: '20px', fontSize: '12px', fontWeight: 600,
      pointerEvents: 'none', boxShadow: '0 2px 8px rgba(0,0,0,.15)',
      display: gpsStatus === 'ok' ? 'none' : 'block',
      transition: 'top 0.2s',
    },
  }

  return (
    <div style={styles.root}>
      <div style={styles.left}>
        <ChatPanel userLocation={userLocation} onPlanReady={onPlanReady} />
      </div>
      <div style={styles.right}>

        <WeatherWidget
          userLocation={userLocation}
          stops={stops}
          onReplan={handleReplan}
          replanLoading={replanLoading}
        />

        {showWarning && (
          <div style={styles.weatherCard}>
            <div style={styles.cardHeader}>
              <span style={{ fontSize: '22px' }}>{isThunderstorm ? '⛈️' : '🌧️'}</span>
              <div>
                <div style={{ fontWeight: 700, fontSize: '14px', lineHeight: 1 }}>Weather Alert</div>
                <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '2px' }}>
                  {weatherWarnings.length} stop{weatherWarnings.length > 1 ? 's' : ''} affected
                </div>
              </div>
              <div style={styles.sevBadge}>{severity}</div>
            </div>

            {weatherWarnings.map((w, i) => (
              <div key={i} style={styles.stopRow}>
                <div style={styles.stopRowTop}>
                  <span style={{ fontSize: '16px' }}>{weatherEmoji(w)}</span>
                  <span style={styles.stopName}>{w.stop_name}</span>
                  <span style={styles.stopTime}>~{w.arrival_time}</span>
                </div>
                <div style={styles.stopDetail}>
                  <span>{w.description.charAt(0).toUpperCase() + w.description.slice(1)}</span>
                  <span style={styles.probPill}>{Math.round(w.precipitation_probability)}% chance</span>
                </div>
              </div>
            ))}

            <div style={styles.actions}>
              <button onClick={handleReplan} disabled={replanLoading} style={styles.replanBtn}>
                {replanLoading ? '…replanning' : '🔄 Replan for indoor spots'}
              </button>
              <button onClick={() => setWeatherDismissed(true)} style={styles.dismissBtn}>
                Dismiss
              </button>
            </div>
          </div>
        )}

        <div style={styles.gpsBadge}>
          {gpsStatus === 'denied'    && '📍 Using Trivandrum centre — location access was denied'}
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
