import { useEffect, useState } from 'react'
import { Check, DoorOpen, Layers, Play, ScanEye, X } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import './SceneLog.css'

const LIVE_MS = 5000

const STATE_LABEL = { open: 'Open', closed: 'Closed', blocked: 'Blocked' }
const KIND_LABEL = { area: 'Area', gate: 'Gate', exit: 'Fire exit' }

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)

  if (hours > 0) return `${hours} h ${minutes} min`
  if (minutes > 0) return `${minutes} min ${seconds % 60} s`
  return `${seconds} s`
}

function titleCase(text) {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// "Helmet" with a tick when worn and a cross when clearly not worn
function PpeBadges({ ppe }) {
  const entries = Object.entries(ppe || {})

  if (entries.length === 0) {
    return <span className="sl-muted">not checked yet</span>
  }

  return (
    <span className="sl-ppe">
      {entries.map(([item, answer]) => (
        <span key={item} className={`sl-badge ${answer === 'yes' ? 'good' : 'bad'}`}>
          {answer === 'yes' ? <Check size={13} /> : <X size={13} />}
          {titleCase(item)}
        </span>
      ))}
    </span>
  )
}

function SceneLog({ onOpenAlerts }) {
  const [live, setLive] = useState([])
  const [visits, setVisits] = useState({ total: 0, items: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const [clip, setClip] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const [liveResponse, visitsResponse] = await Promise.all([
          fetch('/api/scene/live'),
          fetch('/api/scene/visits?since_hours=24&limit=50'),
        ])

        if (!liveResponse.ok || !visitsResponse.ok) {
          throw new Error('Unable to load the scene log')
        }

        const liveData = await liveResponse.json()
        const visitsData = await visitsResponse.json()

        if (!cancelled) {
          setLive(liveData)
          setVisits(visitsData)
          setError('')
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    const timer = setInterval(load, LIVE_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  // Make the "for how long" numbers tick between refreshes
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  // Play the clip of a scene alert (the same clip is listed under Alerts)
  async function playClip(alertId, title) {
    setClip({ title, url: '', error: '' })
    try {
      const response = await fetch(`/api/violations/${alertId}/clip`)
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not load the clip')
      setClip({ title, url: data.url, error: '' })
    } catch (err) {
      setClip({ title, url: '', error: err.message })
    }
  }

  const zoneCount = live.reduce((sum, camera) => sum + camera.zones.length, 0)

  return (
    <div className="ov-page sl-page">
      <PageHeader
        icon={ScanEye}
        title="Scene Log"
        subtitle="Who is in each zone, for how long, in what safety gear, and what state the gates are in"
        crumbs={['VIGIL', 'Scene Log']}
        onOpenAlerts={onOpenAlerts}
      />

      {error && <p className="error-message">{error}</p>}

      {loading ? (
        <p className="empty-message">Loading the scene log...</p>
      ) : zoneCount === 0 ? (
        <div className="empty-state">
          <h3>No zones yet</h3>
          <p>
            Open a camera under Cameras and draw its zones (a work area, a gate,
            a fire exit). People and gate states are logged here from then on.
          </p>
        </div>
      ) : (
        <>
          {live.map(camera => (
            <section className="sl-camera" key={camera.camera_id}>
              <h2>{camera.camera_name || `Camera ${camera.camera_id}`}</h2>

              <div className="sl-zones">
                {camera.zones.map(zone => {
                  const Icon = zone.kind === 'area' ? Layers : DoorOpen
                  const isGate = zone.kind !== 'area'

                  return (
                    <article className="sl-zone" key={zone.id}>
                      <header>
                        <span className={`sl-kind ${zone.kind}`}>
                          <Icon size={18} />
                        </span>
                        <div>
                          <h3>{zone.name}</h3>
                          <small>{KIND_LABEL[zone.kind] || zone.kind}</small>
                        </div>

                        {isGate && (
                          <span className={`sl-state ${zone.state || 'none'}`}>
                            {zone.state ? STATE_LABEL[zone.state] : 'No reading yet'}
                          </span>
                        )}
                      </header>

                      <p className="sl-count">
                        <strong>{zone.people.length}</strong>{' '}
                        {zone.people.length === 1 ? 'person' : 'people'} here now
                      </p>

                      {zone.people.length > 0 && (
                        <ul className="sl-people">
                          {zone.people.map(person => (
                            <li key={person.track_id}>
                              <strong>Person #{person.track_id}</strong>
                              <span>
                                {formatDuration((now - Date.parse(person.since)) / 1000)}
                              </span>
                              <PpeBadges ppe={person.ppe} />
                              {person.alert_id && (
                                <button
                                  type="button"
                                  className="sl-clip-btn"
                                  onClick={() =>
                                    playClip(person.alert_id, `Person #${person.track_id} in ${zone.name}`)
                                  }
                                >
                                  <Play size={13} /> Clip
                                </button>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </article>
                  )
                })}
              </div>
            </section>
          ))}

          <section className="ov-panel sl-history">
            <div className="ov-panel-head">
              <div>
                <h2>Recent visits</h2>
                <p>The last 24 hours, newest first</p>
              </div>
              <span className="ov-pill">{visits.total} visits</span>
            </div>

            {visits.items.length === 0 ? (
              <div className="ov-empty">
                <h3>No visits yet</h3>
                <p>A visit is logged when someone stays in a zone for a couple of seconds.</p>
              </div>
            ) : (
              <div className="ov-table-wrap">
                <table className="ov-table">
                  <thead>
                    <tr>
                      <th>Entered</th>
                      <th>Camera</th>
                      <th>Zone</th>
                      <th>Person</th>
                      <th>Time there</th>
                      <th>Safety gear</th>
                      <th>Status</th>
                      <th>Clip</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visits.items.map(visit => (
                      <tr key={visit.id}>
                        <td>{new Date(visit.entered_at).toLocaleString()}</td>
                        <td>{visit.camera_name || '-'}</td>
                        <td>{visit.zone_name || 'Deleted zone'}</td>
                        <td>#{visit.track_id}</td>
                        <td>{formatDuration(visit.duration_seconds)}</td>
                        <td>
                          <PpeBadges ppe={visit.ppe} />
                        </td>
                        <td>
                          <span className={`ov-status ${visit.present ? 'online' : ''}`}>
                            {visit.present ? 'Here now' : 'Left'}
                          </span>
                        </td>
                        <td>
                          {visit.alert_id ? (
                            <button
                              type="button"
                              className="sl-clip-btn"
                              onClick={() =>
                                playClip(
                                  visit.alert_id,
                                  `Person #${visit.track_id} in ${visit.zone_name || 'a deleted zone'}`
                                )
                              }
                            >
                              <Play size={13} /> Play
                            </button>
                          ) : (
                            <span className="sl-muted">-</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {clip && (
        <div className="sl-clip-modal" onClick={() => setClip(null)}>
          <div className="sl-clip-card" onClick={event => event.stopPropagation()}>
            <div className="sl-clip-head">
              <h3>{clip.title}</h3>
              <button type="button" onClick={() => setClip(null)} aria-label="Close">
                <X size={18} />
              </button>
            </div>

            {clip.error ? (
              <p className="error-message">{clip.error}</p>
            ) : clip.url ? (
              <video key={clip.url} src={clip.url} controls autoPlay playsInline />
            ) : (
              <p className="sl-muted">Loading clip...</p>
            )}

            <div className="sl-clip-foot">
              <span>The zone is shaded and the person boxed. The same clip is under Alerts.</span>
              <button
                type="button"
                className="sl-clip-btn"
                onClick={() => {
                  setClip(null)
                  onOpenAlerts()
                }}
              >
                Open Alerts
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default SceneLog
