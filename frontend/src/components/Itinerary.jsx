// Itinerary.jsx -- shared, presentational plan UI.
//
// Everything that renders a trip lives here so the SAME look is reused in three
// places: the collapsed card in the chat, the full-plan pop-up, and the saved-
// trip viewer on the home screen. ChatPanel keeps only the conversation logic.

import React, { useState } from 'react'
import { XCircle, Car, Save, Check, ArrowUpRight, PenLine } from 'lucide-react'

export const MEAL_TITLES = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Supper' }
export const MEAL_ORDER  = ['breakfast', 'lunch', 'dinner']
// Rough clock time each meal happens at, used only to slot an unfilled meal
// placeholder into the itinerary at the right chronological spot.
const MEAL_NOMINAL = { breakfast: 8 * 60 + 30, lunch: 13 * 60, dinner: 19 * 60 + 30 }

const toMin = (hhmm) => {
  const [h, m] = String(hhmm || '0:0').split(':').map(Number)
  return h * 60 + m
}

// "YYYY-MM-DD" -> "Today · Sunday, 5 Jul 2026" etc. so the travel day is obvious.
export function formatTripDate(dateStr) {
  if (!dateStr) return 'Date not set'
  const d = new Date(dateStr + 'T00:00:00')
  if (isNaN(d.getTime())) return dateStr
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.round((d - today) / 86400000)
  const long = d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric' })
  if (diff === 0)  return `Today · ${long}`
  if (diff === 1)  return `Tomorrow · ${long}`
  if (diff === -1) return `Yesterday · ${long}`
  return long
}

// A friendly default name for a freshly-made plan (the user can rename it).
export function defaultTripName(plan) {
  const stops = plan.stops || []
  const dest = stops.find(s => s.is_destination)
  if (dest) return `Day out to ${dest.name}`
  const sights = stops.filter(s => !s.is_meal)
  if (sights.length) {
    return sights.length > 1 ? `${sights[0].name} & ${sights.length - 1} more` : sights[0].name
  }
  return 'Trivandrum day trip'
}

// Seed the user's current per-meal selection from the backend's `added` flags.
export function seedSelections(suggestions = {}) {
  const sel = {}
  for (const meal of MEAL_ORDER) {
    const chosen = (suggestions[meal] || []).find(c => c.added)
    if (chosen) sel[meal] = chosen.id
  }
  return sel
}

// Requested meals that can't be served in this trip window (empty suggestion list).
export function emptyMeals(suggestions = {}) {
  return MEAL_ORDER.filter(m => Array.isArray(suggestions?.[m]) && !suggestions[m].length)
}

const DIET_STYLE = {
  veg:    { bg: '#dcfce7', fg: '#166534', label: 'Pure veg' },
  nonveg: { bg: '#fee2e2', fg: '#991b1b', label: 'Non-veg' },
  both:   { bg: '#fef3c7', fg: '#92400e', label: 'Veg & Non-veg' },
}

// The stored insight is the full review file; strip the ID/NAME/CATEGORY header
// and the [RAW_REVIEW_REPOSITORY] marker so only the real review quotes show.
function cleanInsight(text = '') {
  const marker = '[RAW_REVIEW_REPOSITORY]'
  const idx = text.indexOf(marker)
  return (idx !== -1 ? text.slice(idx + marker.length) : text).trim()
}

function Stars({ rating }) {
  if (!rating) return null
  return (
    <span style={{ fontSize: '12px', color: '#f59e0b', fontWeight: 700 }}>
      ★ {Number(rating).toFixed(1)}
    </span>
  )
}

function StopCard({ stop, index, onRemove, busy }) {
  const meal = stop.is_meal
  const dest = stop.is_destination
  const accent = meal ? '#b45309' : dest ? '#7c3aed' : '#2563eb'
  const badge  = meal ? '🍽' : dest ? '🏁' : index + 1
  return (
    <div style={{
      background: meal ? '#fffbeb' : '#fff',
      border: `1.5px solid ${meal ? '#fde68a' : dest ? '#ddd6fe' : '#dbeafe'}`,
      borderRadius: '10px', padding: '10px 12px 10px 14px', marginBottom: '10px',
      boxShadow: '0 1px 3px rgba(37,99,235,0.08)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{
          background: accent, color: '#fff', borderRadius: '50%',
          width: '20px', height: '20px', display: 'inline-flex', alignItems: 'center',
          justifyContent: 'center', fontSize: '11px', fontWeight: 700, flexShrink: 0,
        }}>{badge}</span>
        <strong style={{ fontSize: '15px', color: accent, fontWeight: 700, flex: 1 }}>{stop.name}</strong>
        <span style={{ fontSize: '12px', color: '#6b7280', flexShrink: 0 }}>
          {stop.visit_starts}–{stop.visit_ends}
        </span>
        {onRemove && (
          <button
            onClick={onRemove} disabled={busy} title="Remove from plan"
            style={{
              background: 'none', border: 'none', cursor: busy ? 'default' : 'pointer',
              color: '#c4c4c4', padding: '0 0 0 2px', lineHeight: 1, flexShrink: 0,
              opacity: busy ? 0.4 : 1,
            }}>
            <XCircle size={16} />
          </button>
        )}
      </div>
      {stop.timing_reason && (
        <div style={{ fontSize: '12.5px', color: '#4b5563', margin: '4px 0 0 28px', lineHeight: '1.4' }}>
          {stop.timing_reason}
        </div>
      )}
    </div>
  )
}

// The link between two consecutive stops: a dashed line with a rounded pill in
// the middle showing how long the drive between them takes (and roughly how far).
function TravelConnector({ min, km }) {
  if (min == null) return null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '0 10px', margin: '-2px 0 8px' }}>
      <div style={{ flex: 1, borderTop: '2px dashed #d1d5db' }} />
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: '4px', whiteSpace: 'nowrap',
        background: '#eef2ff', border: '1px solid #c7d2fe', color: '#4338ca',
        borderRadius: '20px', padding: '3px 10px', fontSize: '11px', fontWeight: 700,
      }}>
        <Car size={12} />
        {min} min{km != null ? ` · ${km} km` : ''}
      </div>
      <div style={{ flex: 1, borderTop: '2px dashed #d1d5db' }} />
    </div>
  )
}

function SkippedCard({ place }) {
  return (
    <div style={{
      background: '#fafafa', borderLeft: '4px solid #d1d5db',
      borderRadius: '8px', padding: '8px 12px', marginBottom: '6px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
        <XCircle size={14} color="#9ca3af" style={{ flexShrink: 0 }} />
        <strong style={{ fontSize: '13px', color: '#4b5563' }}>{place.name}</strong>
      </div>
      <div style={{ fontSize: '12px', color: '#9ca3af', paddingLeft: '20px' }}>
        {place.skipped_reason}
      </div>
    </div>
  )
}

// Low-priority "why these didn't make the cut" list -- collapsed by default.
export function SkippedSection({ skipped }) {
  const [open, setOpen] = useState(false)
  if (!skipped?.length) return null
  return (
    <div style={{ marginTop: '2px' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer', padding: '4px',
          fontSize: '11px', fontWeight: 700, color: '#9ca3af',
          textTransform: 'uppercase', letterSpacing: '0.04em',
        }}>
        {open ? '▾' : '▸'} Didn't make the cut ({skipped.length})
      </button>
      {open && skipped.map(p => <SkippedCard key={p.id || p.name} place={p} />)}
    </div>
  )
}

function MealCard({ card, selected, onAdd, busy }) {
  const [showReview, setShowReview] = useState(false)
  const ds = DIET_STYLE[card.diet] || DIET_STYLE.both
  return (
    <div style={{
      background: '#fff', border: `1.5px solid ${selected ? '#16a34a' : '#e5e7eb'}`,
      borderRadius: '10px', padding: '10px 12px', marginBottom: '8px',
      boxShadow: selected ? '0 0 0 2px #16a34a22' : '0 1px 2px rgba(0,0,0,0.04)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <strong style={{ fontSize: '14px', color: '#111', flex: 1 }}>{card.name}</strong>
        <Stars rating={card.rating} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '5px 0' }}>
        <span style={{
          background: ds.bg, color: ds.fg, fontSize: '10.5px', fontWeight: 700,
          padding: '2px 7px', borderRadius: '20px',
        }}>{card.diet_label || ds.label}</span>
        {card.distance_km != null ? (
          <span style={{ fontSize: '11px', color: '#9ca3af' }}>
            {card.distance_km} km from {card.ref_name}
          </span>
        ) : card.detour_min != null ? (
          <span style={{ fontSize: '11px', color: '#9ca3af' }}>
            ~{Math.round(card.detour_min)} min off your route
          </span>
        ) : null}
      </div>
      {card.diet === 'both' && card.diet_note && (
        <div style={{ fontSize: '11.5px', color: '#92400e', marginBottom: '4px' }}>{card.diet_note}</div>
      )}
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <button
          onClick={() => setShowReview(v => !v)}
          style={{
            background: 'none', border: 'none', color: '#2563eb', cursor: 'pointer',
            fontSize: '12px', padding: 0, fontWeight: 600,
          }}>
          {showReview ? 'Hide reviews' : 'View reviews'}
        </button>
        <button
          onClick={onAdd} disabled={busy}
          style={{
            marginLeft: 'auto',
            background: selected ? '#16a34a' : '#2563eb', color: '#fff', border: 'none',
            borderRadius: '8px', padding: '6px 12px', cursor: busy ? 'default' : 'pointer',
            fontSize: '12.5px', fontWeight: 700, opacity: busy ? 0.6 : 1,
          }}>
          {selected ? '✓ Added' : '+ Add to plan'}
        </button>
      </div>
      {showReview && (
        <div style={{
          marginTop: '8px', fontSize: '12px', color: '#4b5563', lineHeight: '1.45',
          maxHeight: '160px', overflowY: 'auto', whiteSpace: 'pre-line',
          background: '#f9fafb', borderRadius: '8px', padding: '8px 10px',
        }}>
          {cleanInsight(card.insight) || 'No review text available.'}
        </div>
      )}
    </div>
  )
}

// "Let Tripy choose": best-rated of the (already near-route) suggestions.
function bestPick(list) {
  return list.reduce((a, b) => {
    const ra = Number(a.rating) || 0, rb = Number(b.rating) || 0
    if (rb !== ra) return rb > ra ? b : a
    return (b.detour_min ?? 0) < (a.detour_min ?? 0) ? b : a
  }, list[0])
}

// An empty slot living INSIDE the itinerary, at the meal's time.
function MealSlot({ meal, onOpen }) {
  const label = MEAL_TITLES[meal].toLowerCase()
  return (
    <button
      onClick={onOpen}
      style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: '10px', textAlign: 'left',
        background: '#fffbeb', border: '1.5px dashed #fbbf24', borderRadius: '10px',
        padding: '11px 13px', marginBottom: '10px', cursor: 'pointer', fontFamily: 'inherit',
      }}>
      <span style={{
        background: '#b45309', color: '#fff', borderRadius: '50%', width: '22px', height: '22px',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', flexShrink: 0,
      }}>🍽</span>
      <span style={{ flex: 1 }}>
        <span style={{ display: 'block', fontWeight: 700, fontSize: '14px', color: '#b45309' }}>Add {label}</span>
        <span style={{ display: 'block', fontSize: '12px', color: '#92400e' }}>
          Tap to pick a spot — Tripy fits it into your route.
        </span>
      </span>
      <span style={{ fontSize: '13px', color: '#b45309', fontWeight: 700, flexShrink: 0 }}>Select ›</span>
    </button>
  )
}

// The big pop-up restaurant picker (opened from a meal slot).
export function MealModal({ meal, cards, selectedId, busy, onPick, onClose }) {
  const title = (MEAL_TITLES[meal] || meal).toLowerCase()
  const closeBtn = {
    background: '#f3f4f6', border: 'none', borderRadius: '50%', width: '30px', height: '30px',
    cursor: 'pointer', fontSize: '15px', color: '#6b7280', flexShrink: 0, lineHeight: 1,
  }
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
        backdropFilter: 'blur(3px)', WebkitBackdropFilter: 'blur(3px)',
        zIndex: 3400, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px',
      }}>
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#fff', borderRadius: '18px', width: 'min(560px, 94vw)', maxHeight: '86vh',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          boxShadow: '0 24px 70px rgba(0,0,0,0.45)',
        }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '16px 18px', borderBottom: '1px solid #f0f0f0' }}>
          <span style={{ fontSize: '24px' }}>🍽</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 800, fontSize: '17px', color: '#111' }}>Choose your {title}</div>
            <div style={{ fontSize: '12px', color: '#6b7280' }}>
              Your pick slots into the route at the right time — one spot per meal.
            </div>
          </div>
          <button onClick={onClose} style={closeBtn} title="Close">✕</button>
        </div>
        <div style={{ padding: '14px 18px', overflowY: 'auto' }}>
          <button
            onClick={() => onPick(bestPick(cards).id)} disabled={busy}
            style={{
              width: '100%', marginBottom: '12px', background: '#b45309', color: '#fff', border: 'none',
              borderRadius: '10px', padding: '11px', cursor: busy ? 'default' : 'pointer',
              fontSize: '13.5px', fontWeight: 700, opacity: busy ? 0.6 : 1,
            }}>
            ✨ Let Tripy pick the best-rated spot
          </button>
          {cards.map(card => (
            <MealCard
              key={card.id} card={card} busy={busy}
              selected={selectedId === card.id}
              onAdd={() => onPick(card.id)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

// A personal note about a visited place -- collapsed to a small prompt until
// tapped, then an auto-growing textarea. Changes flow up immediately (so the
// text never vanishes on re-render); the caller decides when to persist
// (typically on blur, to avoid writing to storage on every keystroke).
function JournalNote({ value, onChange, onBlur }) {
  const [open, setOpen] = useState(!!value)
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={journalToggle}>
        <PenLine size={12} /> Add a journal note
      </button>
    )
  }
  return (
    <textarea
      autoFocus={!value}
      value={value}
      onChange={e => onChange(e.target.value)}
      onBlur={onBlur}
      placeholder="What did you think of this place? Jot down a memory…"
      style={journalTextarea}
    />
  )
}

// The itinerary body: sightseeing + meal stops in time order, drive-time
// connectors between them, and (when interactive) an inline "Add <meal>" slot
// wherever a requested meal hasn't been chosen. onOpenSlot/onRemove absent =>
// read-only (saved-trip view). journal/onJournalChange/onJournalBlur, when
// given, add a per-stop personal note field (used on the saved-trip viewer).
export function ItineraryBlock({
  stops, suggestions, selections, busy, onRemove, onOpenSlot,
  journal, onJournalChange, onJournalBlur,
}) {
  const sugg = suggestions || {}
  const sel  = selections || {}
  const openSlots = onOpenSlot
    ? MEAL_ORDER.filter(m => Array.isArray(sugg[m]) && sugg[m].length && !sel[m])
    : []

  const items = []
  ;(stops || []).forEach(s => items.push({ type: 'stop', time: toMin(s.arrive_at), s }))
  openSlots.forEach(m => items.push({ type: 'slot', time: MEAL_NOMINAL[m], meal: m }))
  items.sort((a, b) => a.time - b.time)

  let n = 0
  return (
    <>
      {items.map(it => {
        if (it.type === 'slot') {
          return <MealSlot key={`slot-${it.meal}`} meal={it.meal} onOpen={() => onOpenSlot(it.meal)} />
        }
        const s = it.s
        const num = (!s.is_meal && !s.is_destination) ? n++ : null
        const key = s.id || s.name
        return (
          <React.Fragment key={key}>
            <TravelConnector min={s.travel_from_prev_min} km={s.travel_from_prev_km} />
            <StopCard
              stop={s} index={num} busy={busy}
              onRemove={onRemove ? () => onRemove(s.id) : null}
            />
            {onJournalChange && (
              <JournalNote
                value={journal?.[key] || ''}
                onChange={(text) => onJournalChange(key, text)}
                onBlur={() => onJournalBlur?.(key)}
              />
            )}
          </React.Fragment>
        )
      })}
    </>
  )
}

// ── Collapsed one-box view of a whole plan (shown in the chat / on cards). ──
export function TripSummaryCard({ plan, name, saved, busy, onOpen, onSave }) {
  const stops  = plan.stops || []
  const sights = stops.filter(s => !s.is_meal)
  const meals  = stops.filter(s => s.is_meal)
  const preview = sights.slice(0, 3).map(s => s.name).join('  →  ')
  return (
    <div
      onClick={onOpen}
      style={{
        alignSelf: 'flex-start', width: '100%', maxWidth: '88%', cursor: 'pointer',
        background: '#fff', border: '1.5px solid #dbeafe', borderRadius: '14px',
        padding: '14px', boxShadow: '0 2px 12px rgba(37,99,235,0.12)',
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
        <span style={{ fontSize: '22px' }}>🗺️</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 800, fontSize: '15px', color: '#111', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {name}
          </div>
          <div style={{ fontSize: '12px', color: '#2563eb', fontWeight: 600 }}>{formatTripDate(plan.trip_date)}</div>
        </div>
      </div>
      <div style={{ fontSize: '12.5px', color: '#6b7280', margin: '9px 0 4px' }}>
        {sights.length} stop{sights.length !== 1 ? 's' : ''}{meals.length ? ` · ${meals.length} meal${meals.length !== 1 ? 's' : ''}` : ''}
      </div>
      {preview && (
        <div style={{ fontSize: '12.5px', color: '#4b5563', lineHeight: 1.4 }}>
          {preview}{sights.length > 3 ? '  …' : ''}
        </div>
      )}
      <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }} onClick={e => e.stopPropagation()}>
        <button onClick={onOpen} style={btn.outline}>
          View full plan <ArrowUpRight size={14} />
        </button>
        {onSave && (
          <button onClick={onSave} disabled={busy || saved} style={saved ? btn.saved : btn.primary}>
            {saved ? <Check size={14} /> : <Save size={14} />}
            {saved ? 'Saved' : 'Save'}
          </button>
        )}
      </div>
    </div>
  )
}

// ── The full plan, as a large pop-up. Editable in the planner (remove / add /
// rename / save); read-only when viewing a saved trip from the home screen. ──
export function FullPlanModal({
  plan, name, editable, onRename, busy, onRemove, onOpenSlot,
  onSave, saved, onClose, onEditInPlanner, onDelete,
  journal, onJournalChange, onJournalBlur,
}) {
  return (
    <div onClick={onClose} style={sheetOverlay}>
      <div onClick={e => e.stopPropagation()} style={sheet}>
        <div style={sheetHeader}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {editable ? (
              <input
                value={name} onChange={e => onRename(e.target.value)}
                placeholder="Name this trip" style={nameInput}
              />
            ) : (
              <div style={{ fontWeight: 800, fontSize: '18px', color: '#111' }}>{name}</div>
            )}
            <div style={{ fontSize: '12.5px', color: '#2563eb', fontWeight: 600, marginTop: '3px' }}>
              {formatTripDate(plan.trip_date)}
            </div>
          </div>
          <button onClick={onClose} style={sheetClose} title="Close">✕</button>
        </div>

        <div style={sheetBody}>
          <div style={sheetLabel}>{onRemove ? 'Your itinerary · tap ✕ to drop a stop' : 'Your itinerary'}</div>
          <ItineraryBlock
            stops={plan.stops} suggestions={plan.meal_suggestions} selections={plan.meal_selections}
            busy={busy} onRemove={onRemove} onOpenSlot={onOpenSlot}
            journal={journal} onJournalChange={onJournalChange} onJournalBlur={onJournalBlur}
          />
          {emptyMeals(plan.meal_suggestions).map(meal => (
            <div key={meal} style={emptyNote}>
              No {MEAL_TITLES[meal].toLowerCase()} spots fit this trip — it ends before {MEAL_TITLES[meal].toLowerCase()} time.
              Extend the end time, or give an earlier {MEAL_TITLES[meal].toLowerCase()} time (e.g. "{MEAL_TITLES[meal].toLowerCase()} at 17:30").
            </div>
          ))}
          <SkippedSection skipped={plan.skipped} />
        </div>

        <div style={sheetFooter}>
          {onDelete && <button onClick={onDelete} style={btn.danger}>Delete</button>}
          {onEditInPlanner && <button onClick={onEditInPlanner} style={btn.secondary}>Edit in planner</button>}
          {onSave && (
            <button onClick={onSave} disabled={busy || saved} style={saved ? btn.saved : btn.primary}>
              {saved ? <Check size={14} /> : <Save size={14} />}
              {saved ? 'Saved' : 'Save trip'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── shared styles ──
const btnBase = {
  flex: 1, borderRadius: '9px', padding: '9px 12px', cursor: 'pointer',
  fontSize: '13px', fontWeight: 700, display: 'inline-flex', alignItems: 'center',
  justifyContent: 'center', gap: '6px',
}
const btn = {
  primary:   { ...btnBase, background: '#2563eb', color: '#fff', border: 'none' },
  outline:   { ...btnBase, background: '#fff', color: '#1f2937', border: '1.5px solid #d1d5db' },
  secondary: { ...btnBase, background: '#eff6ff', color: '#2563eb', border: '1px solid #bfdbfe' },
  saved:     { ...btnBase, background: '#dcfce7', color: '#166534', border: '1px solid #86efac', cursor: 'default' },
  danger:    { ...btnBase, flex: 'initial', background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca' },
}
const sheetOverlay = {
  position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
  backdropFilter: 'blur(3px)', WebkitBackdropFilter: 'blur(3px)',
  zIndex: 3000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px',
}
const sheet = {
  background: '#fff', borderRadius: '18px', width: 'min(600px, 94vw)', maxHeight: '88vh',
  display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 24px 70px rgba(0,0,0,0.45)',
}
const sheetHeader = { display: 'flex', alignItems: 'flex-start', gap: '12px', padding: '18px 20px', borderBottom: '1px solid #f0f0f0' }
const sheetClose  = { background: '#f3f4f6', border: 'none', borderRadius: '50%', width: '30px', height: '30px', cursor: 'pointer', fontSize: '15px', color: '#6b7280', flexShrink: 0, lineHeight: 1 }
const sheetBody   = { padding: '16px 20px', overflowY: 'auto' }
const sheetLabel  = { fontSize: '11px', fontWeight: 700, color: '#2563eb', textTransform: 'uppercase', letterSpacing: '0.04em', margin: '0 0 8px 4px' }
const sheetFooter = { display: 'flex', gap: '8px', padding: '14px 20px', borderTop: '1px solid #f0f0f0' }
const nameInput   = { width: '100%', fontWeight: 800, fontSize: '18px', color: '#111', border: 'none', borderBottom: '2px solid #e5e7eb', outline: 'none', padding: '2px 0', fontFamily: 'inherit' }
const emptyNote   = { fontSize: '12px', color: '#92400e', background: '#fffbeb', border: '1px dashed #fbbf24', borderRadius: '8px', padding: '8px 11px', marginBottom: '8px' }
const journalToggle = {
  display: 'inline-flex', alignItems: 'center', gap: '6px', background: 'none', border: 'none',
  color: '#6b7280', cursor: 'pointer', fontSize: '12px', fontWeight: 600,
  padding: '0 0 12px 6px', fontFamily: 'inherit',
}
const journalTextarea = {
  width: '100%', minHeight: '56px', margin: '-4px 0 12px', padding: '9px 11px',
  border: '1px solid #e5e7eb', borderRadius: '9px', fontSize: '12.5px', lineHeight: 1.5,
  fontFamily: 'inherit', resize: 'vertical', color: '#374151', background: '#fafafa', outline: 'none',
}
