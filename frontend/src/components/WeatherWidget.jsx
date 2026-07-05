import React, { useState, useEffect, useCallback, useRef } from 'react'
import { RefreshCw } from 'lucide-react'

const API = ''
const POLL_MS = 20 * 60 * 1000  // refresh current conditions every 20 min

// WMO weather code -> emoji (matches the backend's WMO_DESCRIPTIONS buckets).
function wxEmoji(code) {
  if (code == null) return '🌡️'
  if ([95, 96, 99].includes(code)) return '⛈️'
  if ([71, 73, 75, 77, 85, 86].includes(code)) return '🌨️'
  if ([80, 81, 82].includes(code)) return '🌦️'
  if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67].includes(code)) return '🌧️'
  if ([45, 48].includes(code)) return '🌫️'
  if (code === 3) return '☁️'
  if (code === 2) return '⛅'
  if (code === 1 || code === 0) return '☀️'
  return '🌡️'
}

const cap = s => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '')

// "YYYY-MM-DD" -> "Tomorrow · Sun, 5 Jul" so the user knows which day the
// per-stop forecast is actually for.
function dayLabel(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr + 'T00:00:00')
  if (isNaN(d.getTime())) return ''
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.round((d - today) / 86400000)
  const wd = d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  if (diff === 0) return `Today · ${wd}`
  if (diff === 1) return `Tomorrow · ${wd}`
  return wd
}

export default function WeatherWidget({ userLocation, stops = [], tripDate, onReplan, replanLoading, onData }) {
  const [data, setData]         = useState(null)
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading]   = useState(false)
  const pollRef = useRef(null)

  const fetchWeather = useCallback(async () => {
    if (!userLocation) return
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/weather`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          lat:   userLocation[0],
          lng:   userLocation[1],
          date:  tripDate || null,
          stops: (stops || []).map(s => ({ name: s.name, lat: s.lat, lng: s.lng, arrive_at: s.arrive_at })),
        }),
      })
      if (res.ok) {
        const json = await res.json()
        setData(json)
        onData?.(json)   // share the per-stop forecast with the map
      }
    } catch { /* keep last reading on transient failure */ }
    finally { setLoading(false) }
  }, [userLocation, stops, tripDate, onData])

  // Refetch when location, the planned stops, or the trip day change, then poll.
  useEffect(() => {
    if (!userLocation) return
    fetchWeather()
    pollRef.current = setInterval(fetchWeather, POLL_MS)
    return () => clearInterval(pollRef.current)
  }, [fetchWeather, userLocation])

  if (!userLocation) return null

  const cur          = data?.current
  const stopRows     = data?.stops || []
  const needsReplan  = data?.needs_replan
  const warnCount    = stopRows.filter(s => s.is_warning).length
  const ready        = !!cur   // only touch cur.* when we actually have it

  const S = {
    box: {
      position: 'absolute', top: '12px', right: '12px', zIndex: 1000,
      width: expanded ? '290px' : 'auto',
      background: 'rgba(255,255,255,0.96)', backdropFilter: 'blur(10px)',
      WebkitBackdropFilter: 'blur(10px)', borderRadius: '14px',
      boxShadow: '0 6px 24px rgba(0,0,0,0.18)', border: '1px solid rgba(0,0,0,0.06)',
      overflow: 'hidden', fontFamily: 'inherit', color: '#111',
    },
    head: {
      display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer',
      padding: '9px 12px', userSelect: 'none',
    },
    temp:  { fontSize: '15px', fontWeight: 800, lineHeight: 1 },
    desc:  { fontSize: '11px', color: '#6b7280', marginTop: '2px' },
    dot:   {
      width: '8px', height: '8px', borderRadius: '50%',
      background: '#ef4444', marginLeft: '2px', flexShrink: 0,
    },
    body:  { borderTop: '1px solid #eef0f3', padding: '10px 12px' },
    sectionLabel: {
      fontSize: '10px', fontWeight: 700, color: '#9ca3af',
      textTransform: 'uppercase', letterSpacing: '0.05em', margin: '2px 0 6px',
    },
    row: {
      display: 'flex', alignItems: 'center', gap: '8px',
      padding: '5px 0', fontSize: '12.5px',
    },
    replanBtn: {
      width: '100%', marginTop: '10px', background: '#2563eb', color: '#fff',
      border: 'none', borderRadius: '9px', padding: '9px', cursor: 'pointer',
      fontWeight: 700, fontSize: '12.5px',
      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '7px',
    },
  }

  return (
    <div style={S.box}>
      <div style={S.head} onClick={() => setExpanded(v => !v)} title="Weather">
        <span style={{ fontSize: '22px' }}>{ready ? wxEmoji(cur.weather_code) : '🌡️'}</span>
        <div>
          <div style={S.temp}>
            {ready ? `${Math.round(cur.temperature)}°C` : (loading ? '…' : '—')}
          </div>
          <div style={S.desc}>
            {ready ? cap(cur.description) : (loading ? 'Loading…' : 'Weather unavailable')}
          </div>
        </div>
        {warnCount > 0 && <span style={S.dot} title={`${warnCount} stop(s) with rain`} />}
        <span style={{ marginLeft: '4px', color: '#9ca3af', fontSize: '11px' }}>
          {expanded ? '▴' : '▾'}
        </span>
      </div>

      {expanded && (
        <div style={S.body}>
          <div style={S.sectionLabel}>Right now · your location</div>
          <div style={S.row}>
            <span style={{ fontSize: '18px' }}>{ready ? wxEmoji(cur.weather_code) : '🌡️'}</span>
            <span style={{ flex: 1 }}>{ready ? cap(cur.description) : (loading ? 'Loading…' : 'Unavailable')}</span>
            {ready && <strong>{Math.round(cur.temperature)}°C</strong>}
          </div>

          {stopRows.length > 0 && (
            <>
              <div style={{ ...S.sectionLabel, marginTop: '10px' }}>
                Along your trip{tripDate ? ` · ${dayLabel(tripDate)}` : ''}
              </div>
              {stopRows.map((s, i) => (
                <div key={i} style={{
                  ...S.row,
                  background: s.is_warning ? '#fef2f2' : 'transparent',
                  borderRadius: '7px', padding: s.is_warning ? '5px 7px' : '5px 0',
                  margin: s.is_warning ? '0 -7px' : 0,
                }}>
                  <span style={{ fontSize: '16px' }}>{wxEmoji(s.weather_code)}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {s.stop_name}
                    </div>
                    <div style={{ fontSize: '11px', color: '#6b7280' }}>
                      ~{s.arrival_time} · {cap(s.description)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', fontSize: '11px', color: s.is_warning ? '#b91c1c' : '#6b7280' }}>
                    {s.temperature != null && <div><strong>{Math.round(s.temperature)}°</strong></div>}
                    {s.precipitation_probability > 0 && <div>{Math.round(s.precipitation_probability)}%☔</div>}
                  </div>
                </div>
              ))}
            </>
          )}

          {needsReplan && onReplan && (
            <button style={S.replanBtn} onClick={onReplan} disabled={replanLoading}>
              <RefreshCw size={14} />
              {replanLoading ? 'Replanning…' : 'Rain expected — replan indoors'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
