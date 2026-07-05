// tripStore.js -- saved-trip persistence for the prototype.
//
// The backend TripStore is in-memory and does NOT survive a restart (documented
// in backend/agents/state.py). For a single-user prototype, the durable "my
// trips" history lives in the browser's localStorage: it survives reloads,
// needs no backend schema, and keeps this feature off the fragile LLM path.
// A multi-device deployment would move this to a real DB behind an API.

const KEY = 'tripy.savedTrips.v1'

export function loadTrips() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY))
    return Array.isArray(raw) ? raw : []
  } catch {
    return []
  }
}

function persist(list) {
  try { localStorage.setItem(KEY, JSON.stringify(list)) } catch { /* quota / private mode */ }
  return list
}

// Upsert by id so re-saving an edited trip updates it instead of duplicating.
export function saveTrip(trip) {
  const list = loadTrips()
  const i = list.findIndex(t => t.id === trip.id)
  if (i >= 0) list[i] = trip
  else list.unshift(trip)
  return persist(list)
}

export function deleteTrip(id) {
  return persist(loadTrips().filter(t => t.id !== id))
}

export function newTripId() {
  return 't_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}
