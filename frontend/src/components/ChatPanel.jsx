import React, { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { Send, Loader2, MapPin, Clock, XCircle } from 'lucide-react'

const API = ''  // vite proxy handles /api -> localhost:8000

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
        // Streaming narrative from Groq
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let text = ''
        setMessages(prev => [...prev, { role: 'assistant', content: '' }])

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          text += decoder.decode(value, { stream: true })
          updateLastAssistant({ content: text })
        }

        if (!text.trim()) {
          updateLastAssistant({ content: "⚠️ I didn't get a response back — try sending that again." })
        }
      } else {
        const data = await res.json()
        setMessages(prev => [...prev, { role: 'assistant', content: data.reply || '(no reply)' }])
      }

      // After chat, also fetch the structured plan for the map + skipped-place cards
      if (userLocation) {
        const planRes = await fetch(`${API}/api/plan`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            query: userMsg.content,
            lat:   userLocation[0],
            lng:   userLocation[1],
            trip_start: extractTime(userMsg.content, 'start') || '09:00',
            trip_end:   extractTime(userMsg.content, 'end')   || '18:00',
          }),
        })
        if (planRes.ok) {
          const plan = await planRes.json()
          if (plan.stops?.length) onPlanReady(plan)
          updateLastAssistant({ skipped: plan.skipped || [] })
        }
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

  // Very simple time extractor -- the LLM does the real parsing via tool calling.
  // This separate regex-based pass only feeds the /api/plan call used for the
  // map + skipped-place cards, so it can occasionally disagree with what the
  // chat reply itself describes if the wording is unusual -- worth knowing if
  // the map ever looks slightly off from the narration.
  function extractTime(text, which) {
    const patterns = text.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/gi) || []
    if (which === 'start' && patterns[0]) return normaliseTime(patterns[0])
    if (which === 'end'   && patterns[1]) return normaliseTime(patterns[1])
    return null
  }

  function normaliseTime(t) {
    const m = t.match(/(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/i)
    if (!m) return null
    let h = parseInt(m[1])
    const min = m[2] ? m[2] : '00'
    const mer = (m[3] || '').toLowerCase()
    if (mer === 'pm' && h < 12) h += 12
    if (mer === 'am' && h === 12) h = 0
    return `${String(h).padStart(2, '0')}:${min}`
  }

  const styles = {
    panel: {
      display: 'flex', flexDirection: 'column', height: '100%',
      background: '#fff', borderRight: '1px solid #e5e7eb',
    },
    header: {
      padding: '16px 20px', borderBottom: '1px solid #e5e7eb',
      background: '#1e3a5f',
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
        <div style={styles.title}>Tripy 🗺️</div>
        <div style={styles.sub}>Your Trivandrum day planner</div>
      </div>

      <div style={styles.msgs}>
        {messages.map((m, i) => (
          <React.Fragment key={i}>
            <div style={styles.bubble(m.role)}>
              <ReactMarkdown components={markdownComponents}>{m.content}</ReactMarkdown>
            </div>
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
