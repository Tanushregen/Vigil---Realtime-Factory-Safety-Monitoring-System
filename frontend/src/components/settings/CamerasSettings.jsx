import { useCallback, useEffect, useState } from 'react'
import './SettingsPanels.css'

async function request(path, options) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(' ') : data.detail
    throw new Error(detail || 'Request failed')
  }
  return data
}

const toText = value => (value == null ? '' : String(value))
const minutesOf = seconds => (seconds == null ? '' : String(Math.round(seconds / 60)))

function CameraCard({ camera, defaults, onSaved, onError }) {
  const initial = {
    analysis: Boolean(camera.analysis_enabled),
    pre: toText(camera.clip_pre_seconds),
    post: toText(camera.clip_post_seconds),
    minutes: minutesOf(camera.segment_seconds),
  }
  const [form, setForm] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')

  const dirty = Object.keys(initial).some(key => form[key] !== initial[key])

  function change(key, value) {
    setDone('')
    setForm(previous => ({ ...previous, [key]: value }))
  }

  async function save() {
    // only what changed is sent: changing the recording length restarts that camera's recorder
    const body = {}
    if (form.analysis !== initial.analysis) body.analysis_enabled = form.analysis
    if (form.pre !== initial.pre) body.clip_pre_seconds = form.pre === '' ? null : Number(form.pre)
    if (form.post !== initial.post) body.clip_post_seconds = form.post === '' ? null : Number(form.post)
    if (form.minutes !== initial.minutes) {
      body.segment_seconds = form.minutes === '' ? null : Math.round(Number(form.minutes) * 60)
    }

    setBusy(true)
    onError('')
    try {
      await request(`/api/cameras/${camera.id}`, { method: 'PATCH', body: JSON.stringify(body) })
      setDone('Saved')
      await onSaved()
    } catch (err) {
      onError(`${camera.name}: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="sp-camera">
      <header>
        <div>
          <h3>{camera.name}</h3>
          <small>{camera.zone || 'No zone'}</small>
        </div>
        <span className={`sp-state ${camera.status === 'online' ? 'ok' : camera.status === 'offline' ? 'error' : ''}`}>
          {camera.status || 'unknown'}
        </span>
      </header>

      <label className="sp-switch">
        <input
          type="checkbox"
          checked={form.analysis}
          disabled={busy}
          onChange={event => change('analysis', event.target.checked)}
        />
        <span>Analyse this camera for violations {form.analysis ? '(on)' : '(off)'}</span>
      </label>

      <div className="sp-camera-grid">
        <label>
          Clip: seconds before
          <input
            className="sp-input"
            type="number"
            min="0"
            max="60"
            step="1"
            placeholder={`default ${defaults.clip_pre_seconds}`}
            value={form.pre}
            disabled={busy}
            onChange={event => change('pre', event.target.value)}
          />
        </label>
        <label>
          Clip: seconds after
          <input
            className="sp-input"
            type="number"
            min="0"
            max="60"
            step="1"
            placeholder={`default ${defaults.clip_post_seconds}`}
            value={form.post}
            disabled={busy}
            onChange={event => change('post', event.target.value)}
          />
        </label>
        <label>
          Recording file length (min)
          <input
            className="sp-input"
            type="number"
            min="1"
            max="1440"
            step="1"
            placeholder={`default ${Math.round(defaults.segment_seconds / 60)}`}
            value={form.minutes}
            disabled={busy}
            onChange={event => change('minutes', event.target.value)}
          />
        </label>
        <label>
          Storage folder
          <input
            className="sp-input wide"
            value={camera.storage_folder || 'created when first used'}
            readOnly
            title="Where this camera's clips and recordings are kept. It stays the same if you rename the camera."
          />
        </label>
      </div>

      <div className="sp-actions tight">
        <button type="button" className="mt-submit" disabled={busy || !dirty} onClick={save}>
          {busy ? 'Saving...' : 'Save'}
        </button>
        {dirty && (
          <button type="button" className="sp-link" onClick={() => setForm(initial)}>
            Discard
          </button>
        )}
        {done && !dirty && <span className="sp-ok">{done}</span>}
      </div>
    </article>
  )
}

function CamerasSettings() {
  const [cameras, setCameras] = useState(null)
  const [defaults, setDefaults] = useState({ clip_pre_seconds: 10, clip_post_seconds: 10, segment_seconds: 7200 })
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [list, defs] = await Promise.all([request('/api/cameras'), request('/api/settings/cameras/defaults')])
      setCameras(list)
      setDefaults(defs)
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  async function setAll(enabled) {
    const targets = (cameras || []).filter(camera => Boolean(camera.analysis_enabled) !== enabled)
    if (targets.length === 0) {
      setMessage(`Analysis is already ${enabled ? 'on' : 'off'} for every camera.`)
      return
    }
    const ok = window.confirm(
      enabled
        ? `Turn analysis on for ${targets.length} camera(s)? Each analysed camera uses the vision model, which costs money.`
        : `Turn analysis off for ${targets.length} camera(s)? No alerts will be raised on them.`
    )
    if (!ok) return

    setBusy(true)
    setError('')
    setMessage('')
    const failed = []
    for (const camera of targets) {
      try {
        await request(`/api/cameras/${camera.id}`, { method: 'PATCH', body: JSON.stringify({ analysis_enabled: enabled }) })
      } catch {
        failed.push(camera.name)
      }
    }
    await load()
    setBusy(false)
    if (failed.length) setError(`Could not change: ${failed.join(', ')}`)
    else setMessage(`Analysis is now ${enabled ? 'on' : 'off'} for ${targets.length} camera(s).`)
  }

  return (
    <div className="sp-stack">
      {error && <p className="error-message">{error}</p>}

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Cameras</h2>
            <p>
              Whether each camera is analysed, how long its alert clips are, and how its footage is stored.
              Leave a number empty to use the default.
            </p>
          </div>
        </div>
        <div className="sp-actions">
          <button type="button" className="sp-link" disabled={busy || !cameras} onClick={() => setAll(true)}>
            Turn analysis on for all
          </button>
          <button type="button" className="sp-link" disabled={busy || !cameras} onClick={() => setAll(false)}>
            Turn analysis off for all
          </button>
          {message && <span className="sp-ok" role="status">{message}</span>}
        </div>
      </section>

      {cameras && cameras.length === 0 && <p className="sp-note">No cameras yet. Add one under Add DVR / NVR.</p>}

      <div className="sp-camera-list">
        {(cameras || []).map(camera => (
          <CameraCard
            key={`${camera.id}-${camera.analysis_enabled}-${camera.clip_pre_seconds}-${camera.clip_post_seconds}-${camera.segment_seconds}`}
            camera={camera}
            defaults={defaults}
            onSaved={load}
            onError={setError}
          />
        ))}
      </div>
    </div>
  )
}

export default CamerasSettings
