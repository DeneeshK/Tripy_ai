import React from 'react'
import { Trash2, MapPin, CalendarDays, Bookmark } from 'lucide-react'
import { formatTripDate } from './Itinerary'

function todayMid() {
  const d = new Date(); d.setHours(0, 0, 0, 0); return d
}

function tripDateValue(t) {
  if (!t.date) return Infinity  // undated trips sort to the end of "upcoming"
  const d = new Date(t.date + 'T00:00:00')
  return isNaN(d.getTime()) ? Infinity : d.getTime()
}

function TripCard({ trip, onOpen, onDelete }) {
  const stops  = trip.plan?.stops || []
  const sights = stops.filter(s => !s.is_meal)
  const meals  = stops.filter(s => s.is_meal)
  const preview = sights.slice(0, 3).map(s => s.name).join('  ·  ')
  return (
    <div
      onClick={() => onOpen(trip)}
      style={{
        background: '#fff', border: '1px solid #e5e7eb', borderRadius: '14px',
        padding: '14px', cursor: 'pointer', boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
        display: 'flex', flexDirection: 'column', gap: '4px',
      }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
        <span style={{ fontSize: '22px', lineHeight: 1 }}>🗺️</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 800, fontSize: '16px', color: '#111', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {trip.name}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '12.5px', color: '#2563eb', fontWeight: 600, marginTop: '2px' }}>
            <CalendarDays size={13} /> {formatTripDate(trip.date)}
          </div>
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(trip.id) }}
          title="Delete trip"
          style={{ background: 'none', border: 'none', color: '#d1d5db', cursor: 'pointer', padding: '2px', flexShrink: 0 }}>
          <Trash2 size={16} />
        </button>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '12.5px', color: '#6b7280', marginLeft: '32px' }}>
        <MapPin size={13} />
        {sights.length} stop{sights.length !== 1 ? 's' : ''}{meals.length ? ` · ${meals.length} meal${meals.length !== 1 ? 's' : ''}` : ''}
      </div>
      {preview && (
        <div style={{ fontSize: '12.5px', color: '#4b5563', lineHeight: 1.4, marginLeft: '32px' }}>
          {preview}{sights.length > 3 ? '  …' : ''}
        </div>
      )}
    </div>
  )
}

function Group({ title, trips, onOpen, onDelete }) {
  if (!trips.length) return null
  return (
    <div style={{ marginBottom: '18px' }}>
      <div style={{ fontSize: '12px', fontWeight: 800, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 10px 2px' }}>
        {title} ({trips.length})
      </div>
      <div style={{ display: 'grid', gap: '10px' }}>
        {trips.map(t => <TripCard key={t.id} trip={t} onOpen={onOpen} onDelete={onDelete} />)}
      </div>
    </div>
  )
}

export default function SavedTripsModal({ trips = [], onClose, onOpen, onDelete }) {
  const today = todayMid().getTime()
  const upcoming = trips.filter(t => tripDateValue(t) >= today).sort((a, b) => tripDateValue(a) - tripDateValue(b))
  const past     = trips.filter(t => tripDateValue(t) <  today).sort((a, b) => tripDateValue(b) - tripDateValue(a))

  return (
    <div onClick={onClose} style={S.overlay}>
      <div onClick={e => e.stopPropagation()} style={S.sheet}>
        <div style={S.header}>
          <Bookmark size={20} color="#2563eb" />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 800, fontSize: '18px', color: '#111' }}>Your saved trips</div>
            <div style={{ fontSize: '12px', color: '#6b7280' }}>Your day-out schedule around Trivandrum.</div>
          </div>
          <button onClick={onClose} style={S.close} title="Close">✕</button>
        </div>

        <div style={S.body}>
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
  overlay: {
    position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
    backdropFilter: 'blur(3px)', WebkitBackdropFilter: 'blur(3px)',
    zIndex: 2800, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px',
  },
  sheet: {
    background: '#fff', borderRadius: '18px', width: 'min(640px, 94vw)', maxHeight: '88vh',
    display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 24px 70px rgba(0,0,0,0.45)',
  },
  header: { display: 'flex', alignItems: 'center', gap: '12px', padding: '18px 20px', borderBottom: '1px solid #f0f0f0' },
  close:  { background: '#f3f4f6', border: 'none', borderRadius: '50%', width: '30px', height: '30px', cursor: 'pointer', fontSize: '15px', color: '#6b7280', flexShrink: 0, lineHeight: 1 },
  body:   { padding: '18px 20px', overflowY: 'auto', background: '#f9fafb' },
  empty:  { textAlign: 'center', color: '#6b7280', fontSize: '14px', lineHeight: 1.6, background: '#fff', border: '1px dashed #d1d5db', borderRadius: '14px', padding: '34px 20px' },
}
