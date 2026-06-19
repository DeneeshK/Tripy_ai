import React, { useState, useRef, useEffect } from 'react'
import { Send, Loader2, MapPin, Clock } from 'lucide-react'

const API = ''  // vite proxy handles /api -> localhost:8000

function StopCard({ stop, index }) {
  return (
    <div style={{
      background: '#f0f7ff', borderLeft: '4px solid #2563eb',
      borderRadius: '8px', padding: '10px 14px', marginBottom: '8px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
        <span style={{
          background: '#2563eb', color: '#fff', borderRadius: '50%',
          width: '22px', height: '22px', display: 'flex', alignItems: 'center',
          justifyContent: 'center', fontSize: '12px', fontWeight: 700, flexShrink: 0,
        }}>{index + 1}</span>
        <strong style={{ fontSize: '14px' }}>{stop.name}</strong>
      </div>
      <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: '#4b5563' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
          <Clock size={12} />{stop.visit_starts} – {stop.visit_ends}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
          <MapPin size={12} />{stop.vibe?.split(',')[0]}
        </span>
      </div>
    </div>
  )
}

export default function ChatPanel({ userLocation, onPlanReady }) {
  const [messages, setMessages] = useState([{
    role: 'assistant',
    content: "Hi! I'm Tripy 👋 Tell me what kind of day you want — I'll plan the whole thing around Trivandrum.\n\nTry something like: *\"I have 9am–6pm today, love temples and old architecture\"*",
  }])
  const [input, setInput]   = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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

      // Check if it's a streaming response or JSON
      const contentType = res.headers.get('content-type') || ''
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
          setMessages(prev => {
            const next = [...prev]
            next[next.length - 1] = { role: 'assistant', content: text }
            return next
          })
        }
      } else {
        const data = await res.json()
        setMessages(prev => [...prev, { role: 'assistant', content: data.reply }])
      }

      // After chat, also fetch the structured plan for the map
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
        const plan = await planRes.json()
        if (plan.stops?.length) {
          onPlanReady(plan)
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

  // Very simple time extractor -- the LLM does the real parsing via tool calling
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
      maxWidth: '85%',
      alignSelf: role === 'user' ? 'flex-end' : 'flex-start',
      background: role === 'user' ? '#2563eb' : '#f3f4f6',
      color: role === 'user' ? '#fff' : '#111',
      borderRadius: role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
      padding: '10px 14px', fontSize: '14px', lineHeight: '1.5',
      whiteSpace: 'pre-wrap',
    }),
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
          <div key={i} style={styles.bubble(m.role)}>
            {m.content}
          </div>
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
