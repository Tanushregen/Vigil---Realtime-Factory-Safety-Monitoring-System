import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import './Alerts.css'

const PAGE_SIZE = 24
const REFRESH_MS = 15000

const ranges = [
  { label: 'Last hour', hours: 1 },
  { label: 'Last 24 hours', hours: 24 },
  { label: 'Last 7 days', hours: 24 * 7 },
  { label: 'All time', hours: 0 },
]

function typeLabel(type) {
  return (type || 'unknown').replace(/_/g, ' ')
}

function timeAgo(isoTime) {
  const seconds = Math.max(0, (Date.now() - new Date(isoTime)) / 1000)

  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`

  return `${Math.floor(seconds / 86400)} d ago`
}

function severity(confidence) {
  if (confidence == null) return 'unknown'
  if (confidence >= 85) return 'high'
  if (confidence >= 70) return 'medium'
  return 'low'
}

function Alerts() {
  const [cameras, setCameras] = useState([])
  const [cameraId, setCameraId] = useState('')
  const [violationType, setViolationType] = useState('')
  const [hours, setHours] = useState(24)

  const [items, setItems] = useState([])
  const [types, setTypes] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')

  const [selected, setSelected] = useState(null)
  const [clipUrl, setClipUrl] = useState('')
  const [clipError, setClipError] = useState('')

  const filters = useRef({ cameraId, violationType, hours })

  const buildUrl = useCallback((offset, limit) => {
    const params = new URLSearchParams({ limit, offset })
    const current = filters.current

    if (current.cameraId) params.set('camera_id', current.cameraId)
    if (current.violationType) {
      params.set('violation_type', current.violationType)
    }
    if (current.hours) params.set('since_hours', current.hours)

    return `/api/violations?${params}`
  }, [])

  // Reload the newest page, keeping as many rows as are already on screen
  const load = useCallback(
    async (keepCount = PAGE_SIZE) => {
      try {
        const response = await fetch(buildUrl(0, Math.max(keepCount, PAGE_SIZE)))

        if (!response.ok) throw new Error('Unable to load alerts')

        const data = await response.json()

        setItems(data.items)
        setTotal(data.total)
        setTypes(data.types)
        setError('')
      } catch {
        setError('Cannot load alerts. Check that the backend is running.')
      } finally {
        setLoading(false)
      }
    },
    [buildUrl]
  )

  // Filters changed: start again from the newest page
  useEffect(() => {
    filters.current = { cameraId, violationType, hours }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    load()
  }, [cameraId, violationType, hours, load])

  // Pick up new violations without a manual refresh
  const countRef = useRef(0)

  useEffect(() => {
    countRef.current = items.length
  }, [items.length])

  useEffect(() => {
    const timer = setInterval(() => load(countRef.current), REFRESH_MS)

    return () => clearInterval(timer)
  }, [load])

  useEffect(() => {
    fetch('/api/cameras')
      .then(response => (response.ok ? response.json() : []))
      .then(setCameras)
      .catch(() => {})
  }, [])

  async function loadMore() {
    setLoadingMore(true)

    try {
      const response = await fetch(buildUrl(items.length, PAGE_SIZE))

      if (!response.ok) throw new Error()

      const data = await response.json()

      setItems(previous => [...previous, ...data.items])
      setTotal(data.total)
    } catch {
      setError('Could not load more alerts.')
    } finally {
      setLoadingMore(false)
    }
  }

  async function openClip(item) {
    setSelected(item)
    setClipUrl('')
    setClipError('')

    try {
      const response = await fetch(`/api/violations/${item.id}/clip`)
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail || 'Could not load the clip')
      }

      setClipUrl(data.url)
    } catch (err) {
      setClipError(err.message)
    }
  }

  function closeClip() {
    setSelected(null)
    setClipUrl('')
  }

  return (
    <div className="ov-page alerts-page">
      <PageHeader
        icon={Bell}
        title="Alerts"
        subtitle="Detected safety violations and their video clips"
        crumbs={['VIGIL', 'Alerts']}
        onRefresh={() => load(items.length)}
      />

      <section className="alerts-filters">
        <label>
          Camera
          <select
            value={cameraId}
            onChange={event => setCameraId(event.target.value)}
          >
            <option value="">All cameras</option>
            {cameras.map(camera => (
              <option key={camera.id} value={camera.id}>
                {camera.name}
              </option>
            ))}
          </select>
        </label>

        <label>
          Violation
          <select
            value={violationType}
            onChange={event => setViolationType(event.target.value)}
          >
            <option value="">All types</option>
            {types.map(type => (
              <option key={type} value={type}>
                {typeLabel(type)}
              </option>
            ))}
          </select>
        </label>

        <label>
          Time
          <select
            value={hours}
            onChange={event => setHours(Number(event.target.value))}
          >
            {ranges.map(range => (
              <option key={range.label} value={range.hours}>
                {range.label}
              </option>
            ))}
          </select>
        </label>

        <span className="alerts-count">
          {total} alert{total === 1 ? '' : 's'}
        </span>
      </section>

      {error && <p className="error-message">{error}</p>}

      {loading ? (
        <p className="empty-message">Loading alerts...</p>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <h3>No alerts found</h3>
          <p>
            Violations appear here with their video clip once the detector
            confirms them.
          </p>
        </div>
      ) : (
        <>
          <div className="alerts-grid">
            {items.map(item => (
              <button
                key={item.id}
                className="alert-card"
                onClick={() => openClip(item)}
              >
                <span className={`alert-severity ${severity(item.confidence)}`}>
                  {item.confidence == null
                    ? 'n/a'
                    : `${Math.round(item.confidence)}%`}
                </span>

                <h3>{typeLabel(item.violation_type)}</h3>
                <p className="alert-camera">
                  {item.camera_name || 'Deleted camera'}
                  {item.zone ? ` · ${item.zone}` : ''}
                </p>
                {item.description && (
                  <p className="alert-description">{item.description}</p>
                )}
                <span className="alert-time">
                  {timeAgo(item.occurred_at)} ·{' '}
                  {new Date(item.occurred_at).toLocaleString()}
                </span>
              </button>
            ))}
          </div>

          {items.length < total && (
            <button
              className="alerts-more"
              onClick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? 'Loading...' : 'Load more'}
            </button>
          )}
        </>
      )}

      {selected && (
        <div className="alert-modal" onClick={closeClip}>
          <div
            className="alert-modal-card"
            onClick={event => event.stopPropagation()}
          >
            <div className="alert-modal-head">
              <div>
                <h2>{typeLabel(selected.violation_type)}</h2>
                <p>
                  {selected.camera_name || 'Deleted camera'} ·{' '}
                  {new Date(selected.occurred_at).toLocaleString()}
                </p>
              </div>

              <button className="alert-modal-close" onClick={closeClip}>
                Close
              </button>
            </div>

            {clipError ? (
              <p className="error-message">{clipError}</p>
            ) : clipUrl ? (
              <video
                key={clipUrl}
                className="alert-video"
                src={clipUrl}
                controls
                autoPlay
                playsInline
              />
            ) : (
              <div className="alert-video alert-video-loading">
                Loading clip...
              </div>
            )}

            <div className="alert-modal-meta">
              <span>
                Confidence:{' '}
                {selected.confidence == null
                  ? 'n/a'
                  : `${Math.round(selected.confidence)}%`}
              </span>
              {selected.description && <span>{selected.description}</span>}
              {clipUrl && (
                <a href={clipUrl} download target="_blank" rel="noreferrer">
                  Download clip
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Alerts
