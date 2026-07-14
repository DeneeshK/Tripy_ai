import React, { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { Send, Loader2, Minus, Maximize2, Minimize2, SquareParking } from 'lucide-react'
import {
  TripSummaryCard, FullPlanModal, MealModal,
  seedSelections, defaultTripName,
} from './Itinerary'
import { newTripId } from '../lib/tripStore'

const API = ''  // vite proxy handles /api -> localhost:8000
const PLAN_TRAILER = '<<<TRIPY_PLAN>>>'  // must match api/main.py PLAN_TRAILER

const markdownComponents = {
  p: ({ children }) => <p style={{ margin: '0 0 6px 0' }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: '4px 0', paddingLeft: '18px' }}>{children}</ul>,
  li: ({ children }) => <li style={{ marginBottom: '2px' }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700 }}>{children}</strong>,
  em: ({ children }) => <em style={{ fontStyle: 'italic' }}>{children}</em>,
}

// A chat message that carries a plan -> the snake-cased `plan` shape the shared
// Itinerary components (and the saved-trip store) expect.
function messageToPlan(m) {
  return {
    trip_id:          m.tripId,
    stops:            m.stops || [],
    skipped:          m.skipped || [],
    meal_suggestions: m.mealSuggestions || {},
    meal_selections:  m.mealSelections || {},
    coords:           m.coords || null,
    trip_date:        m.tripDate || null,
  }
}

export default function ChatPanel({
  userLocation, onPlanReady, onSaveTrip, initialTrip,
  onCollapse, onToggleWide, isWide,
}) {
  const [messages, setMessages] = useState([{
    role: 'assistant',
    content: "Hi! I'm Tripy 👋 Tell me what kind of day you want — I'll plan the whole thing around Trivandrum.\n\nTry something like: *I have 9am–6pm today, love temples and old architecture.*",
  }])
  const [input, setInput]     = useState('')
  const [loading, setLoading] = useState(false)
  // "Parking-friendly" toggle: when on, requires_parking is sent with every
  // chat turn so plan_my_day only considers places with OSM-mapped parking
  // nearby (see backend/rag/enrich_parking.py). The chat model can also turn
  // this on itself mid-conversation ("only places with parking") -- either
  // path sets the same flag, see api/main.py.
  const [requiresParking, setRequiresParking] = useState(false)
  const [mealBusy, setMealBusy] = useState(false)
  const [planModal, setPlanModal] = useState(null)  // message index whose full plan is open
  const [mealModal, setMealModal] = useState(null)  // { msgIndex, tripId, meal } | null
  const bottomRef = useRef(null)
  const seededRef = useRef(null)                    // last saved-trip id dropped into the chat

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Drop a saved trip into the conversation when the user opens one to edit.
  // Guarded by id so it appends once (the planner isn't remounted anymore).
  useEffect(() => {
    if (!initialTrip || seededRef.current === initialTrip.id) return
    seededRef.current = initialTrip.id
    const p = initialTrip.plan || {}
    setMessages(prev => [...prev, {
      role: 'assistant',
      content: `Here's your saved trip **${initialTrip.name}**. Tell me what to change — add or remove a stop, swap a meal — or keep planning.`,
      stops: p.stops || [], skipped: p.skipped || [],
      mealSuggestions: p.meal_suggestions || {}, mealSelections: p.meal_selections || {},
      tripId: p.trip_id, tripDate: p.trip_date, coords: p.coords,
      tripName: initialTrip.name, savedId: initialTrip.id, savedAt: initialTrip.createdAt, saved: true,
    }])
    if ((p.stops || []).length) onPlanReady(p)
  }, [initialTrip])  // eslint-disable-line react-hooks/exhaustive-deps

  function updateLastAssistant(patch) {
    setMessages(prev => {
      const next = [...prev]
      const i = next.length - 1
      if (next[i]?.role === 'assistant') next[i] = { ...next[i], ...patch }
      return next
    })
  }

  function updateMessageAt(index, patch) {
    setMessages(prev => {
      const next = [...prev]
      if (next[index]) next[index] = { ...next[index], ...patch }
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
          requires_parking: requiresParking,
        }),
      })

      const contentType = res.headers.get('content-type') || ''

      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try { detail = (await res.json()).detail || detail } catch {}
        setMessages(prev => [...prev, { role: 'assistant', content: `⚠️ ${detail}` }])
        return
      }

      if (contentType.includes('text/plain')) {
        // Streaming narrative from Groq, with a structured-plan JSON trailer.
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
          patch.tripDate        = plan.trip_date
          patch.coords          = plan.coords
          patch.tripName        = defaultTripName(plan)
          patch.saved           = false
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
          coords:          plan.coords ?? messages[msgIndex]?.coords,
          tripDate:        plan.trip_date ?? messages[msgIndex]?.tripDate,
          saved:           false,
        })
        if (plan.stops?.length) onPlanReady(plan)
      }
    } catch { /* leave the previous plan in place on failure */ }
    finally { setMealBusy(false) }
  }

  // ✕ on a stop -> re-plan without it.
  async function removeStop(msgIndex, tripId, id) {
    if (!tripId || mealBusy) return
    setMealBusy(true)
    try {
      const res = await fetch(`${API}/api/trip/${tripId}/remove`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ id }),
      })
      if (res.ok) {
        const plan = await res.json()
        updateMessageAt(msgIndex, {
          stops:           plan.stops || [],
          skipped:         plan.skipped || [],
          mealSuggestions: plan.meal_suggestions || {},
          mealSelections:  seedSelections(plan.meal_suggestions),
          coords:          plan.coords ?? messages[msgIndex]?.coords,
          tripDate:        plan.trip_date ?? messages[msgIndex]?.tripDate,
          saved:           false,
        })
        if (plan.stops?.length) onPlanReady(plan)
      }
    } catch { /* leave the previous plan in place on failure */ }
    finally { setMealBusy(false) }
  }

  // Persist the plan on a message to the saved-trips store (localStorage).
  function saveCurrent(i) {
    const m = messages[i]
    if (!m) return
    const plan = messageToPlan(m)
    const id = m.savedId || newTripId()
    const name = m.tripName || defaultTripName(plan)
    const trip = { id, name, date: m.tripDate || null, createdAt: m.savedAt || new Date().toISOString(), plan }
    onSaveTrip?.(trip)
    updateMessageAt(i, { savedId: id, saved: true, savedAt: trip.createdAt, tripName: name })
  }

  const styles = {
    panel: {
      display: 'flex', flexDirection: 'column', height: '100%',
      background: '#fff', borderRight: '1px solid #e5e7eb',
      borderRadius: '16px', overflow: 'hidden',
    },
    header: {
      display: 'flex', alignItems: 'center', gap: '10px',
      padding: '10px 14px 10px 8px', borderBottom: '1px solid #1f2937', background: '#000',
    },
    headerBtn: {
      background: 'rgba(255,255,255,0.12)', color: '#fff', border: 'none',
      borderRadius: '8px', width: '30px', height: '30px', cursor: 'pointer',
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
    },
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
    toggleRow: {
      padding: '8px 16px 0', display: 'flex',
    },
    parkingChip: (active) => ({
      display: 'flex', alignItems: 'center', gap: '5px',
      padding: '5px 11px', borderRadius: '20px', cursor: 'pointer',
      border: `1.5px solid ${active ? '#2563eb' : '#d1d5db'}`,
      background: active ? '#eff6ff' : '#fff',
      color: active ? '#2563eb' : '#6b7280',
      fontSize: '12.5px', fontWeight: 600,
    }),
    inputRow: {
      padding: '8px 16px 12px', borderTop: '1px solid #e5e7eb',
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '2px', flexShrink: 0 }}>
          <img src="/logo.png" alt="Tripy logo" style={{ height: '88px', width: 'auto', display: 'block' }} />
          <img src="/title.png" alt="Tripy" style={{ height: '90px', width: 'auto', display: 'block' }} />
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '6px' }}>
          {onToggleWide && (
            <button onClick={onToggleWide} style={styles.headerBtn} title={isWide ? 'Shrink chat' : 'Widen chat'}>
              {isWide ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
            </button>
          )}
          {onCollapse && (
            <button onClick={onCollapse} style={styles.headerBtn} title="Minimise chat">
              <Minus size={16} />
            </button>
          )}
        </div>
      </div>

      <div style={styles.msgs}>
        {messages.map((m, i) => (
          <React.Fragment key={i}>
            <div style={styles.bubble(m.role)}>
              <ReactMarkdown components={markdownComponents}>{m.content}</ReactMarkdown>
            </div>
            {m.stops?.length > 0 && (
              <TripSummaryCard
                plan={messageToPlan(m)}
                name={m.tripName || defaultTripName(messageToPlan(m))}
                saved={!!m.saved}
                busy={mealBusy}
                onOpen={() => setPlanModal(i)}
                onSave={onSaveTrip ? () => saveCurrent(i) : null}
              />
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

      <div style={styles.toggleRow}>
        <button
          style={styles.parkingChip(requiresParking)}
          onClick={() => setRequiresParking(v => !v)}
          title="Only plan places with parking nearby"
        >
          <SquareParking size={14} />
          Parking-friendly
        </button>
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

      {planModal != null && messages[planModal]?.stops?.length > 0 && (
        <FullPlanModal
          plan={messageToPlan(messages[planModal])}
          name={messages[planModal].tripName || defaultTripName(messageToPlan(messages[planModal]))}
          editable
          onRename={(name) => updateMessageAt(planModal, { tripName: name })}
          busy={mealBusy}
          onRemove={messages[planModal].tripId ? (id) => removeStop(planModal, messages[planModal].tripId, id) : null}
          onOpenSlot={(meal) => setMealModal({ msgIndex: planModal, tripId: messages[planModal].tripId, meal })}
          onSave={onSaveTrip ? () => saveCurrent(planModal) : null}
          saved={!!messages[planModal].saved}
          onClose={() => setPlanModal(null)}
        />
      )}

      {mealModal && (
        <MealModal
          meal={mealModal.meal}
          cards={messages[mealModal.msgIndex]?.mealSuggestions?.[mealModal.meal] || []}
          selectedId={messages[mealModal.msgIndex]?.mealSelections?.[mealModal.meal]}
          busy={mealBusy}
          onClose={() => setMealModal(null)}
          onPick={(placeId) => {
            addMeal(mealModal.msgIndex, mealModal.tripId, mealModal.meal, placeId)
            setMealModal(null)
          }}
        />
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
    </div>
  )
}
