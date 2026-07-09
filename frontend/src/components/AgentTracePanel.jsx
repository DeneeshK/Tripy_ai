import React from 'react'
import { Bot, Route, CloudSun, Clock, X } from 'lucide-react'

// One entry per agent decision, newest first. Mirrors backend/agents/graph.py's
// three orchestrator nodes -- see _log_trace() there for what populates this.
const AGENT_META = {
  'Trip Planning Agent':      { icon: Route,    color: '#2563eb' },
  'Weather Monitoring Agent': { icon: CloudSun, color: '#0ea5e9' },
  'Schedule Monitoring Agent': { icon: Clock,    color: '#ea580c' },
}

function timeAgo(iso) {
  if (!iso) return ''
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.round(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  return `${hrs}h ago`
}

const styles = {
  fab: {
    position: 'absolute', bottom: '22px', right: '22px', zIndex: 1250,
    display: 'flex', alignItems: 'center', gap: '8px',
    background: '#111827', color: '#fff', border: 'none', borderRadius: '24px',
    padding: '11px 20px', cursor: 'pointer', fontSize: '14px', fontWeight: 700,
    boxShadow: '0 8px 26px rgba(0,0,0,0.35)',
  },
  badge: {
    background: '#2563eb', borderRadius: '999px', fontSize: '11px', fontWeight: 800,
    padding: '1px 7px', lineHeight: '15px',
  },
  panel: {
    position: 'absolute', top: '16px', right: '16px', bottom: '16px', width: '360px',
    zIndex: 1300, display: 'flex', flexDirection: 'column',
    background: 'rgba(10, 15, 28, 0.95)',
    backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
    border: '1px solid rgba(255,255,255,0.08)', borderRadius: '16px',
    boxShadow: '0 10px 40px rgba(0,0,0,0.45)', color: '#f1f5f9', overflow: 'hidden',
  },
  header: {
    display: 'flex', alignItems: 'flex-start', gap: '10px', padding: '16px 16px 12px',
    borderBottom: '1px solid rgba(255,255,255,0.08)', flexShrink: 0,
  },
  title: { fontWeight: 700, fontSize: '15px', lineHeight: 1.2 },
  subtitle: { fontSize: '11.5px', color: '#94a3b8', marginTop: '3px', lineHeight: 1.4 },
  closeBtn: {
    marginLeft: 'auto', background: 'rgba(255,255,255,0.07)', border: 'none',
    borderRadius: '50%', width: '26px', height: '26px', cursor: 'pointer',
    color: '#cbd5e1', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
  },
  body: { overflowY: 'auto', padding: '12px', flex: 1, display: 'flex', flexDirection: 'column', gap: '10px' },
  card: {
    background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: '12px', padding: '11px 12px',
  },
  cardTop: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' },
  agentName: { fontWeight: 700, fontSize: '12.5px', flex: 1 },
  when: { fontSize: '11px', color: '#94a3b8', flexShrink: 0 },
  summary: { fontSize: '12.5px', color: '#e2e8f0', lineHeight: 1.45 },
  detailList: { marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '4px' },
  detailRow: {
    fontSize: '11.5px', color: '#94a3b8', lineHeight: 1.4,
    background: 'rgba(0,0,0,0.2)', borderRadius: '7px', padding: '5px 8px',
  },
  empty: { padding: '28px 16px', textAlign: 'center', color: '#94a3b8', fontSize: '13px', lineHeight: 1.5 },
}

export function AgentTraceFab({ count, onClick }) {
  return (
    <button style={styles.fab} onClick={onClick} title="See what Tripy's agents have been doing">
      <Bot size={18} />
      Agent Activity
      {count > 0 && <span style={styles.badge}>{count}</span>}
    </button>
  )
}

export default function AgentTracePanel({ trace, onClose }) {
  const entries = [...(trace || [])].reverse()  // newest first
  return (
    <div style={styles.panel}>
      <div style={styles.header}>
        <div>
          <div style={styles.title}>Agent Activity</div>
          <div style={styles.subtitle}>What Tripy's agents just did, and why.</div>
        </div>
        <button style={styles.closeBtn} onClick={onClose} title="Close"><X size={14} /></button>
      </div>
      <div style={styles.body}>
        {entries.length === 0 && (
          <div style={styles.empty}>
            No agent activity yet — plan a trip and this will fill up with what the
            Planning, Weather and Schedule agents decide as your day unfolds.
          </div>
        )}
        {entries.map((e, i) => {
          const meta = AGENT_META[e.agent] || { icon: Bot, color: '#64748b' }
          const Icon = meta.icon
          const details = (e.detail || []).filter(d => d.reason)
          return (
            <div key={i} style={styles.card}>
              <div style={styles.cardTop}>
                <Icon size={15} color={meta.color} />
                <span style={styles.agentName}>{e.agent}</span>
                <span style={styles.when}>{timeAgo(e.at)}</span>
              </div>
              <div style={styles.summary}>{e.summary}</div>
              {details.length > 0 && (
                <div style={styles.detailList}>
                  {details.slice(0, 4).map((d, j) => (
                    <div key={j} style={styles.detailRow}><strong>{d.name}</strong> — {d.reason}</div>
                  ))}
                  {details.length > 4 && (
                    <div style={styles.detailRow}>+{details.length - 4} more</div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
