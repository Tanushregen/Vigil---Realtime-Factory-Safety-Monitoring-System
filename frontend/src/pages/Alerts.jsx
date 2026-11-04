import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, Building2, Folder, LayoutGrid, List } from 'lucide-react'
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
  const [groups, setGroups] = useState([])
  const [groupId, setGroupId] = useState('')
  const [view, setView] = useState('list')
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

  const filters = useRef({ groupId, cameraId, violationType, hours })

  const buildUrl = useCallback((offset, limit) => {
    const params = new URLSearchParams({ limit, offset })
    const current = filters.current

    if (current.groupId === 'ungrouped') params.set('ungrouped', 'true')
    else if (current.groupId) params.set('group_id', current.groupId)
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
    filters.current = { groupId, cameraId, violationType, hours }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    load()
  }, [groupId, cameraId, violationType, hours, load])

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

    fetch('/api/groups')
      .then(response => (response.ok ? response.json() : []))
      .then(setGroups)
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

  // The camera list follows the chosen group
  const groupedCameraIds = new Set(groups.flatMap(group => group.cameraIds))
  const chosenGroup = groups.find(group => group.id === groupId)

  const visibleCameras =
    groupId === 'ungrouped'
      ? cameras.filter(camera => !groupedCameraIds.has(camera.id))
      : chosenGroup
        ? cameras.filter(camera => chosenGroup.cameraIds.includes(camera.id))
        : cameras

  // "By group" view: one section per group, alerts of ungrouped cameras last
  const sections = []

  if (view === 'grouped') {
    const byGroup = new Map()
    const ungroupedItems = []

    for (const item of items) {
      if (item.groups.length === 0) {
        ungroupedItems.push(item)
        continue
      }

      for (const group of item.groups) {
        if (!byGroup.has(group.id)) {
          byGroup.set(group.id, { id: group.id, name: group.name, items: [] })
        }

        byGroup.get(group.id).items.push(item)
      }
    }

    sections.push(
      ...[...byGroup.values()].sort((a, b) => a.name.localeCompare(b.name))
    )

    if (ungroupedItems.length > 0) {
      sections.push({ id: 'ungrouped', name: 'No group', items: ungroupedItems })
    }
  }

  async function sendReview(item, verdict) {
    // clicking the active verdict again clears it
    const next = item.review === verdict ? null : verdict
    try {
      const response = await fetch(`/api/violations/${item.id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ verdict: next }),
      })
      if (!response.ok) throw new Error('Could not save your answer')
      setItems(previous =>
        previous.map(row => (row.id === item.id ? { ...row, review: next } : row))
      )
      setSelected(previous =>
        previous && previous.id === item.id ? { ...previous, review: next } : previous
      )
    } catch (err) {
      setClipError(err.message)
    }
  }

  function renderCard(item, key) {
    return (
      <button
        key={key}
        className="alert-card"
        onClick={() => openClip(item)}
      >
        <span className={`alert-severity ${severity(item.confidence)}`}>
          {item.confidence == null ? 'n/a' : `${Math.round(item.confidence)}%`}
        </span>

        <h3>{typeLabel(item.violation_type)}</h3>
        <p className="alert-camera">
          {item.camera_name || 'Deleted camera'}
          {item.zone ? ` · ${item.zone}` : ''}
        </p>

        <p className="alert-group">
          <Building2 size={15} />
          {item.groups.length > 0
            ? item.groups.map(group => group.name).join(', ')
            : 'No group'}
        </p>

        {item.description && (
          <p className="alert-description">{item.description}</p>
        )}

        {item.clip_dir && (
          <p className="alert-path" title={item.clip_dir}>
            <Folder size={15} />
            <span>{item.clip_dir}</span>
          </p>
        )}

        {item.review && (
          <span className={`alert-review ${item.review}`}>
            {item.review === 'true' ? 'Confirmed' : 'False alert'}
          </span>
        )}

        <span className="alert-time">
          {timeAgo(item.occurred_at)} ·{' '}
          {new Date(item.occurred_at).toLocaleString()}
        </span>
      </button>
    )
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
          Group
          <select
            value={groupId}
            onChange={event => {
              setGroupId(event.target.value)
              setCameraId('')
            }}
          >
            <option value="">All groups</option>
            {groups.map(group => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
            <option value="ungrouped">No group</option>
          </select>
        </label>

        <label>
          Camera
          <select
            value={cameraId}
            onChange={event => setCameraId(event.target.value)}
          >
            <option value="">All cameras</option>
            {visibleCameras.map(camera => (
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

        <div className="alerts-view" role="group" aria-label="Show alerts as">
          <button
            className={view === 'list' ? 'selected' : ''}
            onClick={() => setView('list')}
            aria-pressed={view === 'list'}
          >
            <List size={16} /> Individual
          </button>

          <button
            className={view === 'grouped' ? 'selected' : ''}
            onClick={() => setView('grouped')}
            aria-pressed={view === 'grouped'}
          >
            <LayoutGrid size={16} /> By group
          </button>
        </div>

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
          {view === 'grouped' ? (
            sections.map(section => (
              <section className="alerts-section" key={section.id}>
                <h2 className="alerts-section-title">
                  <Building2 size={20} />
                  {section.name}
                  <span>
                    {section.items.length} alert
                    {section.items.length === 1 ? '' : 's'}
                  </span>
                </h2>

                <div className="alerts-grid">
                  {section.items.map(item =>
                    renderCard(item, `${section.id}-${item.id}`)
                  )}
                </div>
              </section>
            ))
          ) : (
            <div className="alerts-grid">
              {items.map(item => renderCard(item, item.id))}
            </div>
          )}

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
                  {selected.camera_name || 'Deleted camera'}
                  {selected.groups.length > 0 &&
                    ` · ${selected.groups.map(group => group.name).join(', ')}`}{' '}
                  · {new Date(selected.occurred_at).toLocaleString()}
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
              {selected.clip_dir && (
                <span className="alert-path" title={selected.clip_dir}>
                  <Folder size={15} />
                  <span>{selected.clip_dir}</span>
                </span>
              )}
              {clipUrl && (
                <a href={clipUrl} download target="_blank" rel="noreferrer">
                  Download clip
                </a>
              )}
              <span className="alert-verdict">
                Was this a real violation?
                <button
                  type="button"
                  className={selected.review === 'true' ? 'on good' : ''}
                  onClick={() => sendReview(selected, 'true')}
                >
                  Yes, real
                </button>
                <button
                  type="button"
                  className={selected.review === 'false' ? 'on bad' : ''}
                  onClick={() => sendReview(selected, 'false')}
                >
                  No, false alert
                </button>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Alerts
