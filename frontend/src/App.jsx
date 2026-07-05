import React, { useState, useEffect, useCallback, useRef } from 'react'
import TripMap from './components/TripMap'
import ChatPanel from './components/ChatPanel'
import WeatherWidget from './components/WeatherWidget'
import SavedTripsModal from './components/SavedTripsModal'
import { FullPlanModal } from './components/Itinerary'
import { loadTrips, saveTrip, deleteTrip } from './lib/tripStore'
import { RefreshCw, Bookmark } from 'lucide-react'

const API = ''
const POLL_INTERVAL_MS = 30 * 60 * 1000  // 30 minutes

export default function App() {
  const [userLocation, setUserLocation] = useState(null)
  const [stops, setStops]   = useState([])
  const [route, setRoute]   = useState(null)
  const [gpsStatus, setGpsStatus] = useState('requesting')
  const [tripId, setTripId] = useState(null)
  const [tripDate, setTripDate] = useState(null)
  const [weatherWarnings, setWeatherWarnings] = useState([])
  const [replanLoading, setReplanLoading] = useState(false)
  const [weatherDismissed, setWeatherDismissed] = useState(false)
  const [mapConfig, setMapConfig] = useState({ owm_api_key: '', stadia_api_key: '' })
  const [chatOpen, setChatOpen]   = useState(true)
  const [chatWidth, setChatWidth] = useState(380)
  const [resizing, setResizing]   = useState(false)
  const [gpsDismissed, setGpsDismissed] = useState(false)
  const [weatherData, setWeatherData] = useState(null)   // shared per-stop forecast (widget -> map)
  // Saved trips (opened from a button, not a separate home screen).
  const [savedOpen, setSavedOpen] = useState(false)      // saved-trips overlay open
  const [savedTrips, setSavedTrips] = useState(() => loadTrips())
  const [openTrip, setOpenTrip]   = useState(null)       // saved trip open in the viewer modal
  const [initialTrip, setInitialTrip] = useState(null)   // seed the planner with this trip
  const pollRef = useRef(null)
  const resizingRef = useRef(false)

  const handleSaveTrip   = useCallback((trip) => setSavedTrips(saveTrip(trip)), [])
  const handleDeleteTrip = useCallback((id) => {
    setSavedTrips(deleteTrip(id))
    setOpenTrip(t => (t && t.id === id ? null : t))
  }, [])

  const CHAT_MIN = 320
  const CHAT_LEFT = 16               // matches styles.left `left`
  const isWide = chatWidth >= 560

  // Drag the right edge of the chat panel to resize it; toggle button snaps
  // between the default width and a wide reading width.
  const startResize = useCallback((e) => {
    e.preventDefault()
    resizingRef.current = true
    setResizing(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  const toggleWide = useCallback(() => {
    setChatWidth(w => (w >= 560 ? 380 : Math.min(760, Math.round(window.innerWidth * 0.5))))
  }, [])

  useEffect(() => {
    const onMove = (e) => {
      if (!resizingRef.current) return
      const max = Math.min(window.innerWidth - 120, 900)
      setChatWidth(Math.max(CHAT_MIN, Math.min(max, e.clientX - CHAT_LEFT)))
    }
    const onUp = () => {
      if (!resizingRef.current) return
      resizingRef.current = false
      setResizing(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

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
    if (plan.trip_date) setTripDate(plan.trip_date)
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
      position: 'absolute', top: '16px', left: '16px', bottom: '16px', width: `${chatWidth}px`,
      display: 'flex', flexDirection: 'column', zIndex: 500,
      borderRadius: '16px', boxShadow: '0 10px 40px rgba(0,0,0,0.35)',
    },
    resizeHandle: {
      position: 'absolute', top: 0, right: '-5px', width: '12px', height: '100%',
      cursor: 'col-resize', zIndex: 600,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    },
    resizeGrip: {
      width: '4px', height: '46px', borderRadius: '3px',
      background: 'rgba(255,255,255,0.65)', boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
    },
    chatFab: {
      position: 'absolute', left: '20px', bottom: '20px', zIndex: 1200,
      width: '58px', height: '58px', borderRadius: '50%', border: 'none',
      background: '#2563eb', color: '#fff', cursor: 'pointer', fontSize: '24px',
      boxShadow: '0 8px 24px rgba(37,99,235,0.5)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
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
      background: '#fff', color: '#374151',
      padding: '8px 10px 8px 14px', borderRadius: '12px', fontSize: '12.5px', fontWeight: 600,
      boxShadow: '0 4px 18px rgba(0,0,0,0.16)', border: '1px solid #e5e7eb',
      display: 'flex', alignItems: 'center', gap: '10px', maxWidth: '90vw',
      transition: 'top 0.2s',
    },
    gpsClose: {
      background: '#f3f4f6', border: 'none', borderRadius: '50%',
      width: '20px', height: '20px', cursor: 'pointer', color: '#6b7280', fontSize: '13px',
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, lineHeight: 1,
    },
    savedFab: {
      position: 'absolute', bottom: '22px', left: '50%', transform: 'translateX(-50%)',
      zIndex: 1100, display: 'flex', alignItems: 'center', gap: '8px',
      background: '#111827', color: '#fff', border: 'none', borderRadius: '24px',
      padding: '11px 20px', cursor: 'pointer', fontSize: '14px', fontWeight: 700,
      boxShadow: '0 8px 26px rgba(0,0,0,0.35)',
    },
  }

  // Per-stop forecast keyed by stop name, for the little weather badges on the map pins.
  const stopWeather = {}
  for (const r of weatherData?.stops || []) stopWeather[r.stop_name] = r

  return (
    <div style={styles.root}>
      {chatOpen && (
        <div style={styles.left}>
          <ChatPanel
            userLocation={userLocation}
            onPlanReady={onPlanReady}
            onSaveTrip={handleSaveTrip}
            initialTrip={initialTrip}
            onCollapse={() => setChatOpen(false)}
            onToggleWide={toggleWide}
            isWide={isWide}
          />
          <div style={styles.resizeHandle} onMouseDown={startResize} title="Drag to resize">
            <div style={styles.resizeGrip} />
          </div>
        </div>
      )}
      {!chatOpen && (
        <button style={styles.chatFab} onClick={() => setChatOpen(true)} title="Open chat">💬</button>
      )}
      {resizing && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 2000, cursor: 'col-resize' }} />
      )}
      <div style={styles.right}>

        <WeatherWidget
          userLocation={userLocation}
          stops={stops}
          tripDate={tripDate}
          onReplan={handleReplan}
          replanLoading={replanLoading}
          onData={setWeatherData}
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
                <RefreshCw size={14} />
                {replanLoading ? 'Replanning…' : 'Replan for indoor spots'}
              </button>
              <button onClick={() => setWeatherDismissed(true)} style={styles.dismissBtn}>
                Dismiss
              </button>
            </div>
          </div>
        )}

        {gpsStatus !== 'ok' && !gpsDismissed && (
          <div style={styles.gpsBadge}>
            <span>
              {gpsStatus === 'denied'     && '📍 Using Trivandrum centre — location access was denied'}
              {gpsStatus === 'requesting' && '📍 Getting your location…'}
            </span>
            <button style={styles.gpsClose} onClick={() => setGpsDismissed(true)} title="Dismiss">✕</button>
          </div>
        )}

        <TripMap
          userLocation={userLocation}
          stops={stops}
          route={route}
          stadiaApiKey={mapConfig.stadia_api_key || ''}
          owmApiKey={mapConfig.owm_api_key || ''}
          stopWeather={stopWeather}
        />
      </div>

      <button style={styles.savedFab} onClick={() => setSavedOpen(true)} title="Your saved plans">
        <Bookmark size={17} /> Your saved plans
      </button>

      {savedOpen && (
        <SavedTripsModal
          trips={savedTrips}
          onClose={() => setSavedOpen(false)}
          onOpen={(t) => setOpenTrip(t)}
          onDelete={handleDeleteTrip}
        />
      )}

      {openTrip && (
        <FullPlanModal
          plan={openTrip.plan}
          name={openTrip.name}
          editable={false}
          onClose={() => setOpenTrip(null)}
          onEditInPlanner={() => { setInitialTrip(openTrip); setOpenTrip(null); setSavedOpen(false); setChatOpen(true) }}
          onDelete={() => handleDeleteTrip(openTrip.id)}
        />
      )}
    </div>
  )
}
