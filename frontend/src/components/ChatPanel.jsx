import React, { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { Send, Loader2, MapPin, Clock, XCircle } from 'lucide-react'

const API = ''  // vite proxy handles /api -> localhost:8000

function StopCard({ stop, index }) {
  const meal = stop.is_meal
  const accent = meal ? '#b45309' : '#2563eb'
  return (
    <div style={{
      background: meal ? '#fffbeb' : '#fff', border: `1.5px solid ${meal ? '#fde68a' : '#dbeafe'}`,
      borderRadius: '10px', padding: '10px 14px', marginBottom: '10px',
      boxShadow: '0 1px 3px rgba(37,99,235,0.08)',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
        <span style={{
          background: accent, color: '#fff', borderRadius: '50%',
          width: '20px', height: '20px', display: 'inline-flex', alignItems: 'center',
          justifyContent: 'center', fontSize: '11px', fontWeight: 700, flexShrink: 0,
        }}>{meal ? '🍽' : index + 1}</span>
        <strong style={{ fontSize: '15px', color: accent, fontWeight: 700 }}>{stop.name}</strong>
      </div>
      <div style={{ fontSize: '12px', color: '#6b7280', margin: '4px 0 0 28px' }}>
        {stop.visit_starts} – {stop.visit_ends}
      </div>
      {stop.timing_reason && (
        <div style={{ fontSize: '12.5px', color: '#4b5563', margin: '4px 0 0 28px', lineHeight: '1.4' }}>
          {stop.timing_reason}
        </div>
      )}
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

const PLAN_TRAILER = '<<<TRIPY_PLAN>>>'  // must match api/main.py PLAN_TRAILER
const MEAL_TITLES = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Supper' }
const MEAL_ORDER  = ['breakfast', 'lunch', 'dinner']

// Seed the user's current per-meal selection from the backend's `added` flags.
function seedSelections(suggestions = {}) {
  const sel = {}
  for (const meal of MEAL_ORDER) {
    const chosen = (suggestions[meal] || []).find(c => c.added)
    if (chosen) sel[meal] = chosen.id
  }
  return sel
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
        {card.detour_min != null && (
          <span style={{ fontSize: '11px', color: '#9ca3af' }}>
            ~{Math.round(card.detour_min)} min off your route
          </span>
        )}
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

// "Let Tripy choose": of the (already near-route) suggestions, pick the
// best-rated one, tie-broken by least detour.
function bestPick(list) {
  return list.reduce((a, b) => {
    const ra = Number(a.rating) || 0, rb = Number(b.rating) || 0
    if (rb !== ra) return rb > ra ? b : a
    return (b.detour_min ?? 0) < (a.detour_min ?? 0) ? b : a
  }, list[0])
}

function MealSuggestions({ suggestions, selections, onAdd, busy }) {
  const meals = MEAL_ORDER.filter(m => suggestions?.[m]?.length)
  const empty = MEAL_ORDER.filter(m => Array.isArray(suggestions?.[m]) && !suggestions[m].length)
  if (!meals.length && !empty.length) return null
  return (
    <div style={{ alignSelf: 'flex-start', maxWidth: '88%', width: '100%' }}>
      {empty.map(meal => (
        <div key={meal} style={{ fontSize: '12px', color: '#92400e', background: '#fffbeb', border: '1px dashed #fbbf24', borderRadius: '8px', padding: '8px 11px', marginBottom: '8px' }}>
          No {MEAL_TITLES[meal].toLowerCase()} spots fit this trip — it ends before {MEAL_TITLES[meal].toLowerCase()} time. Extend the end time, or give an earlier {MEAL_TITLES[meal].toLowerCase()} time (e.g. "{MEAL_TITLES[meal].toLowerCase()} at 17:30").
        </div>
      ))}
      {meals.map(meal => {
        const list   = suggestions[meal]
        const picked = selections?.[meal]
        return (
          <div key={meal} style={{ marginBottom: '8px' }}>
            <div style={{
              fontSize: '11px', fontWeight: 700, color: '#b45309',
              textTransform: 'uppercase', letterSpacing: '0.04em', margin: '6px 0 6px 4px',
            }}>
              🍽 {MEAL_TITLES[meal]} — pick one
            </div>

            {!picked && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap',
                background: '#fffbeb', border: '1px dashed #fbbf24', borderRadius: '8px',
                padding: '7px 10px', marginBottom: '8px',
              }}>
                <span style={{ fontSize: '12px', color: '#92400e', flex: 1 }}>
                  You haven't picked a {MEAL_TITLES[meal].toLowerCase()} spot yet.
                </span>
                <button
                  onClick={() => onAdd(meal, bestPick(list).id)} disabled={busy}
                  style={{
                    background: '#b45309', color: '#fff', border: 'none', borderRadius: '8px',
                    padding: '5px 11px', cursor: busy ? 'default' : 'pointer',
                    fontSize: '12px', fontWeight: 700, opacity: busy ? 0.6 : 1, whiteSpace: 'nowrap',
                  }}>
                  ✨ Let Tripy choose
                </button>
              </div>
            )}

            {list.map(card => (
              <MealCard
                key={card.id} card={card} busy={busy}
                selected={picked === card.id}
                onAdd={() => onAdd(meal, card.id)}
              />
            ))}
          </div>
        )
      })}
    </div>
  )
}

const markdownComponents = {
  p: ({ children }) => <p style={{ margin: '0 0 6px 0' }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: '4px 0', paddingLeft: '18px' }}>{children}</ul>,
  li: ({ children }) => <li style={{ marginBottom: '2px' }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700 }}>{children}</strong>,
  em: ({ children }) => <em style={{ fontStyle: 'italic' }}>{children}</em>,
}

export default function ChatPanel({ userLocation, onPlanReady }) {
  const [messages, setMessages] = useState([{
    role: 'assistant',
    content: "Hi! I'm Tripy 👋 Tell me what kind of day you want — I'll plan the whole thing around Trivandrum.\n\nTry something like: *I have 9am–6pm today, love temples and old architecture.*",
  }])
  const [input, setInput]   = useState('')
  const [loading, setLoading] = useState(false)
  const [mealBusy, setMealBusy] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function updateLastAssistant(patch) {
    setMessages(prev => {
      const next = [...prev]
      const i = next.length - 1
      if (next[i]?.role === 'assistant') next[i] = { ...next[i], ...patch }
      return next
    })
  }

  async function send() {
    if (!input.trim() || loading) return
    const userMsg = { role: 'user', content: input.trim() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const res = await fetch(`${API}/api/chat`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          messages: [...messages, userMsg].filter(m => m.role !== 'system'),
          lat: userLocation?.[0] ?? 8.5241,
          lng: userLocation?.[1] ?? 76.9366,
        }),
      })

      const contentType = res.headers.get('content-type') || ''

      if (!res.ok) {
        // Errors raised before streaming starts (bad key, Groq request
        // failed, etc.) come back as plain JSON -- surface the real
        // message instead of leaving the bubble blank.
        let detail = `HTTP ${res.status}`
        try { detail = (await res.json()).detail || detail } catch {}
        setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ ${detail}` }])
        return
      }

      if (contentType.includes('text/plain')) {
        // Streaming narrative from Groq, with a structured-plan JSON trailer
        // appended after PLAN_TRAILER. We show only the narrative while streaming
        // and parse the trailer once the stream ends.
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let raw = ''
        setMessages(prev => [...prev, { role: 'assistant', content: '' }])

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          raw += decoder.decode(value, { stream: true })
          const cut = raw.indexOf(PLAN_TRAILER)
          updateLastAssistant({ content: cut === -1 ? raw : raw.slice(0, cut) })
        }

        const cut = raw.indexOf(PLAN_TRAILER)
        const narrative = (cut === -1 ? raw : raw.slice(0, cut)).trim()
        let plan = null
        if (cut !== -1) {
          try { plan = JSON.parse(raw.slice(cut + PLAN_TRAILER.length)) } catch { /* ignore */ }
        }

        const patch = { content: narrative || "⚠️ I didn't get a response back — try sending that again." }
        if (plan) {
          patch.stops           = plan.stops || []
          patch.skipped         = plan.skipped || []
          patch.mealSuggestions = plan.meal_suggestions || {}
          patch.mealSelections  = seedSelections(plan.meal_suggestions)
          patch.tripId          = plan.trip_id
        }
        updateLastAssistant(patch)
        if (plan && plan.stops?.length) onPlanReady(plan)
      } else {
        // Non-streaming JSON: the bot asked a clarifying question (no plan yet).
        const data = await res.json()
        setMessages(prev => [...prev, { role: 'assistant', content: data.reply || '(no reply)' }])
      }
    } catch (err) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Sorry, something went wrong: ${err.message}`,
      }])
    } finally {
      setLoading(false)
    }
  }

  function updateMessageAt(index, patch) {
    setMessages(prev => {
      const next = [...prev]
      if (next[index]) next[index] = { ...next[index], ...patch }
      return next
    })
  }

  // "Add" on a meal card -> re-plan with that restaurant anchored. One per meal.
  async function addMeal(msgIndex, tripId, meal, placeId) {
    if (!tripId || mealBusy) return
    const current = messages[msgIndex]?.mealSelections || {}
    const selections = { ...current, [meal]: placeId }
    setMealBusy(true)
    try {
      const res = await fetch(`${API}/api/trip/${tripId}/meals`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ selections }),
      })
      if (res.ok) {
        const plan = await res.json()
        updateMessageAt(msgIndex, {
          stops:           plan.stops || [],
          skipped:         plan.skipped || [],
          mealSuggestions: plan.meal_suggestions || {},
          mealSelections:  selections,
        })
        if (plan.stops?.length) onPlanReady(plan)
      }
    } catch { /* leave the previous plan in place on failure */ }
    finally { setMealBusy(false) }
  }

  const styles = {
    panel: {
      display: 'flex', flexDirection: 'column', height: '100%',
      background: '#fff', borderRight: '1px solid #e5e7eb',
      borderRadius: '16px', overflow: 'hidden',
    },
    header: {
      display: 'flex', alignItems: 'center', gap: '12px',
      padding: '12px 16px', borderBottom: '1px solid #1f2937',
      background: '#000',
    },
    title: { color: '#fff', fontWeight: 700, fontSize: '20px', letterSpacing: '-0.3px' },
    sub:   { color: '#93c5fd', fontSize: '12px', marginTop: '2px' },
    msgs: {
      flex: 1, overflowY: 'auto', padding: '16px',
      display: 'flex', flexDirection: 'column', gap: '12px',
    },
    bubble: (role) => ({
      maxWidth: '88%',
      alignSelf: role === 'user' ? 'flex-end' : 'flex-start',
      background: role === 'user' ? '#2563eb' : '#f3f4f6',
      color: role === 'user' ? '#fff' : '#111',
      borderRadius: role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
      padding: '10px 14px', fontSize: '14px', lineHeight: '1.5',
    }),
    skippedWrap: {
      alignSelf: 'flex-start', maxWidth: '88%', marginTop: '-4px',
    },
    stopsLabel: {
      fontSize: '11px', fontWeight: 700, color: '#2563eb',
      textTransform: 'uppercase', letterSpacing: '0.04em',
      margin: '6px 0 6px 4px',
    },
    skippedLabel: {
      fontSize: '11px', fontWeight: 700, color: '#9ca3af',
      textTransform: 'uppercase', letterSpacing: '0.04em',
      margin: '6px 0 6px 4px',
    },
    inputRow: {
      padding: '12px 16px', borderTop: '1px solid #e5e7eb',
      display: 'flex', gap: '8px', alignItems: 'flex-end',
    },
    textarea: {
      flex: 1, padding: '10px 14px', borderRadius: '20px',
      border: '1.5px solid #d1d5db', fontSize: '14px',
      outline: 'none', resize: 'none', fontFamily: 'inherit',
      maxHeight: '120px', lineHeight: '1.4',
    },
    sendBtn: {
      background: '#2563eb', border: 'none', borderRadius: '50%',
      width: '40px', height: '40px', cursor: 'pointer',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      flexShrink: 0, color: '#fff',
    },
  }

  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <img src="/logo.png" alt="Tripy logo" style={{ height: '88px', width: 'auto', display: 'block' }} />
        <img src="/title.png" alt="Tripy" style={{ height: '90px', width: 'auto', display: 'block' }} />
      </div>

      <div style={styles.msgs}>
        {messages.map((m, i) => (
          <React.Fragment key={i}>
            <div style={styles.bubble(m.role)}>
              <ReactMarkdown components={markdownComponents}>{m.content}</ReactMarkdown>
            </div>
            {m.stops?.length > 0 && (
              <div style={styles.skippedWrap}>
                <div style={styles.stopsLabel}>Your itinerary, with reasons</div>
                {m.stops.map((s, idx) => <StopCard key={s.name} stop={s} index={idx} />)}
              </div>
            )}
            {m.mealSuggestions && (
              <MealSuggestions
                suggestions={m.mealSuggestions}
                selections={m.mealSelections}
                busy={mealBusy}
                onAdd={(meal, placeId) => addMeal(i, m.tripId, meal, placeId)}
              />
            )}
            {m.skipped?.length > 0 && (
              <div style={styles.skippedWrap}>
                <div style={styles.skippedLabel}>Didn't make the cut</div>
                {m.skipped.map(p => <SkippedCard key={p.name} place={p} />)}
              </div>
            )}
          </React.Fragment>
        ))}
        {loading && (
          <div style={{ ...styles.bubble('assistant'), color: '#9ca3af' }}>
            <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={styles.inputRow}>
        <textarea
          style={styles.textarea}
          rows={1}
          placeholder="Tell me your plans…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
        />
        <button style={styles.sendBtn} onClick={send} disabled={loading}>
          <Send size={16} />
        </button>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
    </div>
  )
}
