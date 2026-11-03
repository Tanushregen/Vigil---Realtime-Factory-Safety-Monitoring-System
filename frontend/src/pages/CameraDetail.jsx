
import { useState } from 'react'

const zones = [
  'Shop floor',
  'Press area',
  'Gate',
  'Storage',
  'Fire exit',
  'Office',
  'Other',
]

function CameraDetail({ camera, onBack, onDeleted, onUpdated }) {
  const [name, setName] = useState(camera.name || '')
  const [zone, setZone] = useState(camera.zone || 'Other')
  const [analysisEnabled, setAnalysisEnabled] = useState(
    camera.analysis_enabled ?? false
  )
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  async function handleSave() {
    if (!name.trim()) {
      setMessage('Camera name cannot be empty.')
      return
    }
    setSaving(true)
    setMessage('')

    try {
      const response = await fetch(`/api/cameras/${camera.id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name,
          zone,
          analysis_enabled: analysisEnabled,
        }),
      })

      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail || 'Failed to update camera')
      }

      onUpdated({
        ...camera,
        name,
        zone,
        analysis_enabled: analysisEnabled,
      })

      setMessage('Camera updated successfully.')
    } catch (error) {
      setMessage(error.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    const confirmed = window.confirm(
      'Are you sure you want to delete this camera?'
    )

    if (!confirmed) return

    setDeleting(true)
    setMessage('')

    try {
      const response = await fetch(`/api/cameras/${camera.id}`, {
        method: 'DELETE',
      })

      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail || 'Failed to delete camera')
      }

      onDeleted(camera.id)
    } catch (error) {
      setMessage(error.message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="detail-page">
      <button className="back-button" onClick={onBack}>
        ← Back to Cameras
      </button>

      <h1>{name}</h1>
      <p className="subtitle">Camera details and live monitoring</p>

      <section className="live-section">
        <h2>Live Camera View</h2>

        <iframe
          key={camera.id}
          title={`Camera ${camera.id} live view`}
          src={`http://127.0.0.1:1984/stream.html?src=cam${camera.id}&mode=mse,webrtc`}
          allow="autoplay; fullscreen"
          className="live-player"
        />
      </section>

      <section className="detail-card">
        <h2>Camera Configuration</h2>

        <label>
          Camera Name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <label>
          Zone
          <select
            value={zone}
            onChange={(event) => setZone(event.target.value)}
          >
            {zones.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <label className="analysis-toggle">
          <input
            type="checkbox"
            checked={analysisEnabled}
            onChange={(event) =>
              setAnalysisEnabled(event.target.checked)
            }
          />
          Use for safety checks
        </label>

        <button
          className="save-button"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : 'Save Changes'}
        </button>

        {message && <p className="form-message">{message}</p>}
      </section>

      <section className="detail-card stream-info">
        <h2>Stream Information</h2>
        <p><strong>Channel:</strong> {camera.channel}</p>
        <p><strong>Status:</strong> {camera.status || 'Unknown'}</p>
        <p><strong>Codec:</strong> {camera.codec || 'Not available'}</p>
        <p>
          <strong>Resolution:</strong>{' '}
          {camera.width && camera.height
            ? `${camera.width} × ${camera.height}`
            : 'Not available'}
        </p>
        <p><strong>FPS:</strong> {camera.fps || 'Not available'}</p>
        <p>
          <strong>Last Seen:</strong>{' '}
          {camera.last_seen
            ? new Date(camera.last_seen).toLocaleString()
            : 'Never'}
        </p>
      </section>

     <button
      className="delete-button"
      onClick={handleDelete}
      disabled={deleting}
    >
      {deleting ? 'Deleting...' : 'Delete Camera'}
    </button>
        
    </div>
  )
}

export default CameraDetail
