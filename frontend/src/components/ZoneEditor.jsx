import { useCallback, useEffect, useState } from 'react'
import { DoorOpen, Layers, MapPin, Pencil, RefreshCw, Trash2, Undo2 } from 'lucide-react'
import './ZoneEditor.css'

const KINDS = [
  { value: 'area', label: 'Area', hint: 'a work area, such as the press area' },
  { value: 'gate', label: 'Gate', hint: 'its open / closed / blocked state is logged' },
  { value: 'exit', label: 'Fire exit', hint: 'its open / closed / blocked state is logged' },
]

const KIND_ICON = { area: Layers, gate: DoorOpen, exit: DoorOpen }

function polygonPoints(polygon) {
  return polygon.map(([x, y]) => `${x},${y}`).join(' ')
}

// Draw named zones (areas, gates, exits) on a camera's picture
function ZoneEditor({ cameraId }) {
  const [zones, setZones] = useState([])
  const [points, setPoints] = useState([])
  const [drawing, setDrawing] = useState(false)
  const [naming, setNaming] = useState(false)
  const [form, setForm] = useState({ name: '', kind: 'area' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [snapshot, setSnapshot] = useState(() => Date.now())
  const [pictureFailed, setPictureFailed] = useState(false)

  const loadZones = useCallback(async () => {
    try {
      const response = await fetch(`/api/cameras/${cameraId}/zones`)
      if (!response.ok) throw new Error('Unable to load zones')
      setZones(await response.json())
    } catch (err) {
      setError(err.message)
    }
  }, [cameraId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadZones()
  }, [loadZones])

  function addPoint(event) {
    if (!drawing) return

    // The click and the picture's box are both measured on screen, so this
    // stays right when the app is scaled down by the auto resizer.
    const box = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - box.left) / box.width
    const y = (event.clientY - box.top) / box.height

    setPoints(previous => [
      ...previous,
      [Math.min(1, Math.max(0, +x.toFixed(4))), Math.min(1, Math.max(0, +y.toFixed(4)))],
    ])
  }

  function startDrawing() {
    setError('')
    setPoints([])
    setNaming(false)
    setDrawing(true)
  }

  function cancel() {
    setDrawing(false)
    setNaming(false)
    setPoints([])
    setForm({ name: '', kind: 'area' })
  }

  function finishDrawing() {
    if (points.length < 3) {
      setError('Click at least 3 corners to make a zone.')
      return
    }
    setError('')
    setDrawing(false)
    setNaming(true)
  }

  async function saveZone(event) {
    event.preventDefault()
    setSaving(true)
    setError('')

    try {
      const response = await fetch(`/api/cameras/${cameraId}/zones`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: form.name, kind: form.kind, polygon: points }),
      })
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map(item => item.msg).join(' ')
          : data.detail
        throw new Error(detail || 'Could not save the zone')
      }

      cancel()
      await loadZones()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function deleteZone(zone) {
    const confirmed = window.confirm(
      `Delete the zone "${zone.name}"? Its visit history is deleted too.`
    )
    if (!confirmed) return

    try {
      const response = await fetch(`/api/zones/${zone.id}`, { method: 'DELETE' })
      if (!response.ok) throw new Error('Could not delete the zone')
      await loadZones()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section className="detail-card ze-card">
      <div className="ze-head">
        <div>
          <h2>Zones</h2>
          <p>
            Draw the areas, gates and exits of this camera. The scene log then
            records who is in each zone, for how long, and what state the gates
            are in.
          </p>
        </div>

        <button
          type="button"
          className="ze-refresh"
          onClick={() => {
            setPictureFailed(false)
            setSnapshot(Date.now())
          }}
          title="Take a new picture"
        >
          <RefreshCw size={16} /> New picture
        </button>
      </div>

      <div
        className={`ze-stage ${drawing ? 'drawing' : ''}`}
        onClick={addPoint}
        role={drawing ? 'application' : undefined}
        aria-label={drawing ? 'Click to add a corner of the zone' : undefined}
      >
        <img
          src={`/go2rtc/api/frame.jpeg?src=cam${cameraId}&t=${snapshot}`}
          alt="Camera view for drawing zones"
          draggable="false"
          onError={() => setPictureFailed(true)}
          onLoad={() => setPictureFailed(false)}
        />

        <svg className="ze-overlay" viewBox="0 0 1 1" preserveAspectRatio="none">
          {zones.map(zone => (
            <polygon
              key={zone.id}
              className={`ze-zone ${zone.kind}`}
              points={polygonPoints(zone.polygon)}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {points.length > 1 && (
            <polyline
              className="ze-draft"
              points={polygonPoints(points)}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>

        {zones.map(zone => {
          const x = Math.min(...zone.polygon.map(point => point[0]))
          const y = Math.min(...zone.polygon.map(point => point[1]))

          return (
            <span
              key={zone.id}
              className={`ze-label ${zone.kind}`}
              style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
            >
              {zone.name}
            </span>
          )
        })}

        {points.map(([x, y], index) => (
          <i
            key={index}
            className="ze-dot"
            style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
          />
        ))}

        {pictureFailed && (
          <div className="ze-nopicture">
            No picture from this camera right now. Check that it is online, then
            take a new picture.
          </div>
        )}
      </div>

      {error && <p className="error-message">{error}</p>}

      {!drawing && !naming && (
        <button type="button" className="ze-primary" onClick={startDrawing}>
          <Pencil size={17} /> Draw a zone
        </button>
      )}

      {drawing && (
        <div className="ze-bar">
          <span>
            Click the corners of the zone on the picture ({points.length}{' '}
            point{points.length === 1 ? '' : 's'}, at least 3).
          </span>
          <div>
            <button
              type="button"
              className="ze-secondary"
              onClick={() => setPoints(previous => previous.slice(0, -1))}
              disabled={points.length === 0}
            >
              <Undo2 size={16} /> Undo point
            </button>
            <button type="button" className="ze-secondary" onClick={cancel}>
              Cancel
            </button>
            <button
              type="button"
              className="ze-primary"
              onClick={finishDrawing}
              disabled={points.length < 3}
            >
              Finish zone
            </button>
          </div>
        </div>
      )}

      {naming && (
        <form className="ze-form" onSubmit={saveZone}>
          <label>
            Zone name
            <input
              value={form.name}
              onChange={event => setForm({ ...form, name: event.target.value })}
              placeholder="e.g. Press area"
              maxLength={60}
              required
              autoFocus
            />
          </label>

          <label>
            Type
            <select
              value={form.kind}
              onChange={event => setForm({ ...form, kind: event.target.value })}
            >
              {KINDS.map(kind => (
                <option key={kind.value} value={kind.value}>
                  {kind.label} - {kind.hint}
                </option>
              ))}
            </select>
          </label>

          <div className="ze-form-actions">
            <button type="button" className="ze-secondary" onClick={cancel}>
              Cancel
            </button>
            <button type="submit" className="ze-primary" disabled={saving}>
              {saving ? 'Saving...' : 'Save zone'}
            </button>
          </div>
        </form>
      )}

      <ul className="ze-list">
        {zones.length === 0 ? (
          <li className="ze-empty">
            <MapPin size={18} /> No zones on this camera yet.
          </li>
        ) : (
          zones.map(zone => {
            const Icon = KIND_ICON[zone.kind] || Layers

            return (
              <li key={zone.id}>
                <span className={`ze-kind ${zone.kind}`}>
                  <Icon size={16} />
                  {KINDS.find(kind => kind.value === zone.kind)?.label || zone.kind}
                </span>
                <strong>{zone.name}</strong>
                <button
                  type="button"
                  className="ze-delete"
                  onClick={() => deleteZone(zone)}
                  aria-label={`Delete zone ${zone.name}`}
                >
                  <Trash2 size={16} />
                </button>
              </li>
            )
          })
        )}
      </ul>
    </section>
  )
}

export default ZoneEditor
