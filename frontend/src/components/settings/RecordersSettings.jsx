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

const BRAND_LABEL = { hikvision: 'Hikvision', dahua: 'Dahua', 'cp plus': 'CP Plus' }

function TestResults({ test }) {
  if (!test) return null
  return (
    <div className={`sp-test ${test.ok ? 'ok' : 'error'}`}>
      <strong>{test.ok ? 'Connected' : 'Could not connect'}</strong>
      <ul>
        {test.results.map(item => (
          <li key={item.channel}>
            Channel {item.channel}:{' '}
            {item.valid
              ? `working${item.width ? `, ${item.width}x${item.height}` : ''}${item.fps ? ` at ${item.fps} fps` : ''}`
              : item.error}
          </li>
        ))}
      </ul>
    </div>
  )
}

function RecorderCard({ recorder, brands, isAdmin, onChanged, onError }) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({})
  const [allChannels, setAllChannels] = useState(false)
  const [force, setForce] = useState(false)
  const [test, setTest] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  function startEdit() {
    setForm({
      name: recorder.name, brand: recorder.brand, ip: recorder.ip,
      port: recorder.port, username: recorder.username, password: '',
    })
    setTest(null)
    setForce(false)
    setEditing(true)
    setMessage('')
  }

  // what was typed that differs from what is saved (the password counts when something is typed)
  function changes() {
    const out = {}
    for (const key of ['name', 'brand', 'ip', 'username']) {
      if (form[key] !== recorder[key]) out[key] = form[key]
    }
    if (Number(form.port) !== recorder.port) out.port = Number(form.port)
    if (form.password) out.password = form.password
    return out
  }

  async function runTest() {
    setBusy(true)
    onError('')
    setTest(null)
    try {
      const connection = changes()
      delete connection.name          // a name change is not part of a connection test
      setTest(await request(`/api/recorders/${recorder.id}/test`, {
        method: 'POST',
        body: JSON.stringify({ ...connection, all_channels: allChannels }),
      }))
    } catch (err) {
      onError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    setBusy(true)
    onError('')
    try {
      const result = await request(`/api/recorders/${recorder.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...changes(), force }),
      })
      setEditing(false)
      setMessage(
        result.streams_updated
          ? `Saved. ${result.streams_updated} camera stream(s) now use the new details` +
              (result.streams_failed ? `; ${result.streams_failed} could not be updated.` : '.')
          : 'Saved.'
      )
      await onChanged()
    } catch (err) {
      onError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    const typed = window.prompt(
      `This deletes "${recorder.name}", its ${recorder.cameras.length} camera(s), and ALL their alerts, ` +
        'clips and recordings for good.\n\nType the recorder name to confirm:'
    )
    if (typed === null) return
    if (typed.trim() !== recorder.name) {
      onError('The name did not match, so nothing was deleted.')
      return
    }
    setBusy(true)
    onError('')
    try {
      await request(`/api/recorders/${recorder.id}`, { method: 'DELETE' })
      await onChanged()
    } catch (err) {
      onError(err.message)
      setBusy(false)
    }
  }

  const set = (key, value) => {
    setTest(null)
    setForm(previous => ({ ...previous, [key]: value }))
  }
  const nothingChanged = Object.keys(changes()).length === 0

  return (
    <article className="sp-camera">
      <header>
        <div>
          <h3>{recorder.name}</h3>
          <small>
            {BRAND_LABEL[recorder.brand] || recorder.brand} · {recorder.ip}:{recorder.port} · user {recorder.username}
          </small>
        </div>
        <span className={`sp-state ${recorder.online === recorder.cameras.length && recorder.cameras.length ? 'ok' : recorder.online ? 'warn' : 'error'}`}>
          {recorder.online} of {recorder.cameras.length} online
        </span>
      </header>

      <ul className="sp-chips">
        {recorder.cameras.map(camera => (
          <li key={camera.id} className={camera.status === 'online' ? 'on' : ''}>
            Ch {camera.channel}: {camera.status}
          </li>
        ))}
      </ul>

      {message && <p className="sp-ok" role="status">{message}</p>}

      {!editing && (
        <div className="sp-actions tight">
          {isAdmin ? (
            <>
              <button type="button" className="mt-submit" disabled={busy} onClick={startEdit}>Edit</button>
              <button
                type="button"
                className="sp-link"
                disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  onError('')
                  setTest(null)
                  try {
                    setTest(await request(`/api/recorders/${recorder.id}/test`, {
                      method: 'POST', body: JSON.stringify({ all_channels: true }),
                    }))
                  } catch (err) {
                    onError(err.message)
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                {busy ? 'Testing...' : 'Test connection'}
              </button>
              <button type="button" className="sp-link danger" disabled={busy} onClick={remove}>Delete</button>
            </>
          ) : (
            <span className="sp-note inline">Only an administrator can change recorders.</span>
          )}
        </div>
      )}

      {editing && (
        <div className="sp-edit">
          <div className="sp-camera-grid">
            <label>Name
              <input className="sp-input wide" value={form.name} onChange={e => set('name', e.target.value)} />
            </label>
            <label>Brand
              <select className="sp-input" value={form.brand} onChange={e => set('brand', e.target.value)}>
                {brands.map(brand => <option key={brand} value={brand}>{BRAND_LABEL[brand] || brand}</option>)}
              </select>
            </label>
            <label>IP address
              <input className="sp-input" value={form.ip} onChange={e => set('ip', e.target.value)} />
            </label>
            <label>Port
              <input className="sp-input" type="number" min="1" max="65535" value={form.port}
                onChange={e => set('port', e.target.value)} />
            </label>
            <label>Username
              <input className="sp-input" value={form.username} autoComplete="off"
                onChange={e => set('username', e.target.value)} />
            </label>
            <label>New password
              <input className="sp-input" type="password" value={form.password} autoComplete="new-password"
                placeholder="leave empty to keep" onChange={e => set('password', e.target.value)} />
            </label>
          </div>

          <label className="sp-switch small">
            <input type="checkbox" checked={allChannels} onChange={e => setAllChannels(e.target.checked)} />
            <span>Test every channel, not just channel 1</span>
          </label>

          <TestResults test={test} />

          <label className="sp-switch small">
            <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} />
            <span>Save even if the recorder cannot be reached right now</span>
          </label>

          <div className="sp-actions tight">
            <button type="button" className="mt-submit" disabled={busy || nothingChanged} onClick={save}>
              {busy ? 'Working...' : 'Save'}
            </button>
            <button type="button" className="sp-link" disabled={busy} onClick={runTest}>Test these details</button>
            <button type="button" className="sp-link" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
          </div>
          <p className="sp-note inline">
            Saving different connection details first checks them on channel 1, then points every camera of this
            recorder at the new address. Nothing changes if the check fails.
          </p>
        </div>
      )}

      {!editing && <TestResults test={test} />}
    </article>
  )
}

function RecordersSettings({ isAdmin }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await request('/api/recorders'))
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
    const timer = setInterval(load, 20000)
    return () => clearInterval(timer)
  }, [load])

  return (
    <div className="sp-stack">
      {error && <p className="error-message">{error}</p>}

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Recorders (DVR / NVR)</h2>
            <p>Edit a recorder's address or login, test the connection, or remove it. Add new ones under Add DVR / NVR.</p>
          </div>
        </div>
      </section>

      {data && data.recorders.length === 0 && <p className="sp-note">No recorders yet.</p>}

      <div className="sp-camera-list">
        {(data?.recorders || []).map(recorder => (
          <RecorderCard
            key={recorder.id}
            recorder={recorder}
            brands={data.brands}
            isAdmin={isAdmin}
            onChanged={load}
            onError={setError}
          />
        ))}
      </div>
    </div>
  )
}

export default RecordersSettings
