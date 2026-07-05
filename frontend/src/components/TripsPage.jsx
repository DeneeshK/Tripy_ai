// TripsPage.jsx -- the saved-trips view, as a full page (not a small popup).
// Header carries the app brand top-right; the rest of the page is the trip
// list, grouped Upcoming/Past and sorted by date.

import React from 'react'
import { ArrowLeft, Trash2, MapPin, CalendarDays } from 'lucide-react'
import { formatTripDate } from './Itinerary'

function todayMid() {
  const d = new Date(); d.setHours(0, 0, 0, 0); return d
}

function tripDateValue(t) {
  if (!t.date) return Infinity  // undated trips sort to the end of "upcoming"
  const d = new Date(t.date + 'T00:00:00')
  return isNaN(d.getTime()) ? Infinity : d.getTime()
}

function TripRow({ trip, onOpen, onDelete }) {
  const stops  = trip.plan?.stops || []
  const sights = stops.filter(s => !s.is_meal)
  const meals  = stops.filter(s => s.is_meal)
  const preview = sights.slice(0, 4).map(s => s.name).join('  ·  ')
  return (
    <div onClick={() => onOpen(trip)} style={S.row}>
      <div style={S.rowDate}>
        <CalendarDays size={15} />
        {formatTripDate(trip.date)}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={S.rowName}>{trip.name}</div>
        <div style={S.rowMeta}>
          <MapPin size={13} />
          {sights.length} stop{sights.length !== 1 ? 's' : ''}{meals.length ? ` · ${meals.length} meal${meals.length !== 1 ? 's' : ''}` : ''}
          {trip.journal && Object.values(trip.journal).some(v => v?.trim()) && ' · has notes'}
        </div>
        {preview && <div style={S.rowPreview}>{preview}{sights.length > 4 ? '  …' : ''}</div>}
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(trip.id) }}
        title="Delete trip" style={S.rowDelete}>
        <Trash2 size={16} />
      </button>
    </div>
  )
}

function Group({ title, trips, onOpen, onDelete }) {
  if (!trips.length) return null
  return (
    <div style={{ marginBottom: '28px' }}>
      <div style={S.groupLabel}>{title} ({trips.length})</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {trips.map(t => <TripRow key={t.id} trip={t} onOpen={onOpen} onDelete={onDelete} />)}
      </div>
    </div>
  )
}

export default function TripsPage({ trips = [], onClose, onOpen, onDelete }) {
  const today = todayMid().getTime()
  const upcoming = trips.filter(t => tripDateValue(t) >= today).sort((a, b) => tripDateValue(a) - tripDateValue(b))
  const past     = trips.filter(t => tripDateValue(t) <  today).sort((a, b) => tripDateValue(b) - tripDateValue(a))

  return (
    <div style={S.page}>
      <div style={S.header}>
        <button onClick={onClose} style={S.backBtn} title="Back to planner">
          <ArrowLeft size={18} />
        </button>
        <div style={{ flex: 1 }}>
          <div style={S.title}>Your Trips</div>
          <div style={S.subtitle}>Your day-out schedule around Trivandrum.</div>
        </div>
        <div style={S.brand}>
          <img src="/logo.png" alt="" style={{ height: '38px', width: 'auto', display: 'block' }} />
          <img src="/title.png" alt="Tripy" style={{ height: '38px', width: 'auto', display: 'block' }} />
        </div>
      </div>

      <div style={S.body}>
        <div style={S.inner}>
          {trips.length === 0 ? (
            <div style={S.empty}>
              No saved trips yet.<br />
              Plan a day in the chat, then tap <strong>Save</strong> — it'll show up here.
            </div>
          ) : (
            <>
              <Group title="Upcoming" trips={upcoming} onOpen={onOpen} onDelete={onDelete} />
              <Group title="Past" trips={past} onOpen={onOpen} onDelete={onDelete} />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const S = {
  page: {
    position: 'fixed', inset: 0, zIndex: 2500,
    background: '#f9fafb', display: 'flex', flexDirection: 'column',
  },
  header: {
    display: 'flex', alignItems: 'center', gap: '14px',
    padding: '16px 24px', background: '#000', borderBottom: '1px solid #1f2937', flexShrink: 0,
  },
  backBtn: {
    background: 'rgba(255,255,255,0.12)', color: '#fff', border: 'none',
    borderRadius: '9px', width: '36px', height: '36px', cursor: 'pointer',
    display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  title: { color: '#fff', fontWeight: 800, fontSize: '19px', letterSpacing: '-0.3px' },
  subtitle: { color: '#93c5fd', fontSize: '12.5px', marginTop: '2px' },
  brand: { display: 'flex', alignItems: 'center', gap: '2px', flexShrink: 0 },
  body: { flex: 1, overflowY: 'auto', padding: '28px 24px 60px' },
  inner: { maxWidth: '760px', margin: '0 auto' },
  empty: {
    textAlign: 'center', color: '#6b7280', fontSize: '14px', lineHeight: 1.6,
    background: '#fff', border: '1px dashed #d1d5db', borderRadius: '14px', padding: '44px 20px',
  },
  groupLabel: {
    fontSize: '12px', fontWeight: 800, color: '#9ca3af', textTransform: 'uppercase',
    letterSpacing: '0.06em', margin: '0 0 12px 2px',
  },
  row: {
    display: 'flex', alignItems: 'center', gap: '18px', cursor: 'pointer',
    background: '#fff', border: '1px solid #e5e7eb', borderRadius: '12px',
    padding: '14px 16px', boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
  },
  rowDate: {
    display: 'flex', alignItems: 'center', gap: '6px',
    color: '#2563eb', fontWeight: 700, fontSize: '12.5px', minWidth: '128px', flexShrink: 0,
    lineHeight: 1.3,
  },
  rowName: {
    fontWeight: 800, fontSize: '15.5px', color: '#111',
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  },
  rowMeta: {
    display: 'flex', alignItems: 'center', gap: '5px',
    fontSize: '12.5px', color: '#6b7280', marginTop: '3px',
  },
  rowPreview: { fontSize: '12.5px', color: '#4b5563', marginTop: '4px', lineHeight: 1.4 },
  rowDelete: {
    background: 'none', border: 'none', color: '#d1d5db', cursor: 'pointer',
    padding: '4px', flexShrink: 0,
  },
}
