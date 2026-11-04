import { useEffect, useState } from 'react'
import './SettingsPanels.css'

const GROUPS = [
  {
    id: 'detection',
    title: 'How sure before an alert',
    note: 'These decide how many alerts you get. Lower numbers mean more alerts, and more false ones.',
  },
  {
    id: 'borderline',
    title: 'Doubtful alerts',
    note: 'An alert the model was only moderately sure about is checked on a short clip first.',
  },
  {
    id: 'scene',
    title: 'Scene alerts (zones, PPE, gates)',
    note: 'Alerts raised from the zones drawn on each camera.',
  },
]

const same = (a, b) => (Array.isArray(a) ? a.join(',') === b.join(',') : a === b)
const asText = value => (Array.isArray(value) ? value.join(', ') : value)

async function request(path, options) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || 'Request failed')
  return data
}

function DetectionSettings({ isAdmin }) {
  const [settings, setSettings] = useState([])
  const [draft, setDraft] = useState({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    request('/api/settings/detection')
      .then(data => setSettings(data.settings))
      .catch(err => setError(err.message))
  }, [])

  const current = item => (item.key in draft ? draft[item.key] : item.value)
  const edits = settings.filter(item => item.key in draft && !same(draft[item.key], item.value))
  const anyChanged = settings.some(item => item.changed)

  function edit(item, value) {
    setMessage('')
    setDraft(previous => ({ ...previous, [item.key]: value }))
  }

  async function run(path, options, done) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const data = await request(path, options)
      setSettings(data.settings)
      setDraft({})
      setMessage(done)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function save() {
    const values = Object.fromEntries(edits.map(item => [item.key, draft[item.key]]))
    run('/api/settings/detection', { method: 'PATCH', body: JSON.stringify({ values }) },
      'Saved. It applies from the next scan, with no restart.')
  }

  function reset(keys, done) {
    run('/api/settings/detection/reset', { method: 'POST', body: JSON.stringify({ keys }) }, done)
  }

  function field(item) {
    const value = current(item)
    const disabled = !isAdmin || busy

    if (item.kind === 'bool') {
      return (
        <label className="sp-switch">
          <input
            type="checkbox"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={event => edit(item, event.target.checked)}
          />
          <span>{value ? 'On' : 'Off'}</span>
        </label>
      )
    }

    if (item.kind === 'list') {
      return (
        <input
          className="sp-input wide"
          value={asText(value)}
          disabled={disabled}
          onChange={event => edit(item, event.target.value)}
        />
      )
    }

    return (
      <input
        className="sp-input"
        type="number"
        value={value}
        min={item.min ?? undefined}
        max={item.max ?? undefined}
        step={item.kind === 'int' ? 1 : 'any'}
        disabled={disabled}
        onChange={event => edit(item, event.target.value === '' ? '' : Number(event.target.value))}
      />
    )
  }

  return (
    <div className="sp-stack">
      {!isAdmin && (
        <p className="sp-note">Only an administrator can change these. You can see the current values.</p>
      )}
      {error && <p className="error-message">{error}</p>}

      {GROUPS.map(group => (
        <section className="ov-panel" key={group.id}>
          <div className="ov-panel-head">
            <div>
              <h2>{group.title}</h2>
              <p>{group.note}</p>
            </div>
          </div>

          <div className="sp-rows">
            {settings.filter(item => item.group === group.id).map(item => (
              <div className="sp-row" key={item.key}>
                <div className="sp-label">
                  <strong>{item.label}</strong>
                  <small>{item.help}</small>
                  {item.changed && (
                    <small className="sp-changed">
                      Changed from the default ({asText(item.default).toString() || 'none'}){' '}
                      {isAdmin && (
                        <button
                          type="button"
                          className="sp-link"
                          disabled={busy}
                          onClick={() => reset([item.key], 'Back to the default.')}
                        >
                          Reset
                        </button>
                      )}
                    </small>
                  )}
                </div>
                <div className="sp-field">{field(item)}</div>
              </div>
            ))}
          </div>
        </section>
      ))}

      {isAdmin && (
        <div className="sp-actions">
          <button type="button" className="mt-submit" disabled={busy || edits.length === 0} onClick={save}>
            {edits.length ? `Save ${edits.length} change${edits.length === 1 ? '' : 's'}` : 'Save'}
          </button>
          {edits.length > 0 && (
            <button type="button" className="sp-link" onClick={() => setDraft({})}>
              Discard
            </button>
          )}
          {anyChanged && (
            <button
              type="button"
              className="sp-link"
              disabled={busy}
              onClick={() => {
                if (window.confirm('Put every detection setting back to its default?')) {
                  reset(null, 'Everything is back to its defaults.')
                }
              }}
            >
              Reset all to defaults
            </button>
          )}
          {message && <span className="sp-ok" role="status">{message}</span>}
        </div>
      )}
    </div>
  )
}

export default DetectionSettings
