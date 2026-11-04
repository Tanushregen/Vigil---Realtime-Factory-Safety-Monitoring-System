import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import './SettingsPanels.css'

async function request(path, options) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || 'Request failed')
  return data
}

function formatBytes(bytes) {
  if (bytes == null) return '-'
  if (bytes < 1e6) return `${Math.round(bytes / 1e3)} KB`
  if (bytes < 1e9) return `${(bytes / 1e6).toFixed(1)} MB`
  return `${(bytes / 1e9).toFixed(2)} GB`
}

const KINDS = [
  ['clips', 'Alert clips'],
  ['recordings', 'Continuous recordings'],
  ['metadata', 'Alert details'],
]

function StorageSettings({ isAdmin }) {
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState({})
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async refresh => {
    try {
      setData(await request(`/api/settings/storage${refresh ? '?refresh=true' : ''}`))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load(false)
  }, [load])

  const byKey = Object.fromEntries((data?.settings || []).map(item => [item.key, item]))
  const value = key => (key in draft ? draft[key] : byKey[key]?.value)
  const clipDays = Number(value('clip_retention_days') || 0)
  const recordingDays = Number(value('recording_retention_days') || 0)
  const dirty = Object.keys(draft).some(key => draft[key] !== byKey[key]?.value)

  // What the numbers on screen would delete (nothing is deleted by looking)
  useEffect(() => {
    if (!data) return undefined
    const timer = setTimeout(() => {
      request(`/api/settings/storage/preview?clip_days=${clipDays}&recording_days=${recordingDays}`)
        .then(setPreview)
        .catch(() => setPreview(null))
    }, 400)
    return () => clearTimeout(timer)
  }, [data, clipDays, recordingDays])

  async function save() {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const response = await request('/api/settings/storage', {
        method: 'PATCH',
        body: JSON.stringify({ values: draft }),
      })
      setData(previous => ({ ...previous, settings: response.settings }))
      setDraft({})
      setMessage('Saved. Old files are cleaned up automatically every hour.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function cleanNow() {
    setError('')
    setMessage('')
    try {
      const saved = await request(
        `/api/settings/storage/preview?clip_days=${byKey.clip_retention_days.value}` +
          `&recording_days=${byKey.recording_retention_days.value}`
      )
      if (saved.alerts + saved.recordings === 0) {
        setMessage('Nothing is old enough to delete with the saved settings.')
        return
      }
      const ok = window.confirm(
        `Delete ${saved.alerts} alert(s) with their clips and ${saved.recordings} recording(s) ` +
          'for good? This cannot be undone.'
      )
      if (!ok) return

      setBusy(true)
      const result = await request('/api/settings/storage/cleanup', { method: 'POST' })
      setMessage(
        `Deleted ${result.alerts_deleted} alert(s) and ${result.recordings_deleted} recording(s).` +
          (result.more ? ' There is more; it continues automatically.' : '')
      )
      if (result.errors?.length) setError(`Some files could not be deleted: ${result.errors[0]}`)
      await load(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const usage = data?.usage
  const last = data?.last_cleanup

  return (
    <div className="sp-stack">
      {!isAdmin && <p className="sp-note">Only an administrator can change these.</p>}
      {error && <p className="error-message">{error}</p>}

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>What is stored</h2>
            <p>Footage and clips in your Backblaze B2 bucket</p>
          </div>
          <button type="button" className="sp-link" onClick={() => load(true)}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>

        {usage?.error ? (
          <p className="sp-note">{usage.error}</p>
        ) : (
          <div className="sp-cards">
            {KINDS.map(([id, label]) => (
              <article className="sp-card" key={id}>
                <small>{label}</small>
                <strong>{formatBytes(usage?.kinds?.[id]?.bytes)}</strong>
                <span>{usage?.kinds ? `${usage.kinds[id].count.toLocaleString()} files` : 'loading...'}</span>
              </article>
            ))}
          </div>
        )}
        {usage?.truncated && (
          <p className="sp-note">The bucket is very large; these totals only cover the first 50,000 files.</p>
        )}
      </section>

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Keep only what you need</h2>
            <p>Old files are deleted for good. 0 days means keep everything.</p>
          </div>
        </div>

        <div className="sp-rows">
          {(data?.settings || []).map(item => (
            <div className="sp-row" key={item.key}>
              <div className="sp-label">
                <strong>{item.label}</strong>
                <small>{item.help}</small>
              </div>
              <div className="sp-field">
                {item.kind === 'bool' ? (
                  <label className="sp-switch">
                    <input
                      type="checkbox"
                      checked={Boolean(value(item.key))}
                      disabled={!isAdmin || busy}
                      onChange={event => {
                        setMessage('')
                        setDraft(previous => ({ ...previous, [item.key]: event.target.checked }))
                      }}
                    />
                    <span>{value(item.key) ? 'On' : 'Off'}</span>
                  </label>
                ) : (
                  <input
                    className="sp-input"
                    type="number"
                    min={item.min ?? 0}
                    max={item.max ?? undefined}
                    step={1}
                    value={value(item.key)}
                    disabled={!isAdmin || busy}
                    onChange={event => {
                      setMessage('')
                      setDraft(previous => ({
                        ...previous,
                        [item.key]: event.target.value === '' ? 0 : Number(event.target.value),
                      }))
                    }}
                  />
                )}
              </div>
            </div>
          ))}
        </div>

        <p className="sp-preview">
          {clipDays === 0 && recordingDays === 0
            ? 'With these numbers nothing is ever deleted.'
            : preview
              ? `With these numbers, ${preview.alerts} alert(s) with clips` +
                (recordingDays ? ` and ${preview.recordings ?? '?'} recording(s)` : '') +
                ' are old enough to delete now.' +
                (preview.recordings_error ? ` (${preview.recordings_error})` : '')
              : 'Checking what would be deleted...'}
        </p>

        {isAdmin && (
          <div className="sp-actions">
            <button type="button" className="mt-submit" disabled={busy || !dirty} onClick={save}>
              Save
            </button>
            {dirty && (
              <button type="button" className="sp-link" onClick={() => setDraft({})}>
                Discard
              </button>
            )}
            <button
              type="button"
              className="sp-link danger"
              disabled={busy || dirty || !byKey.clip_retention_days}
              onClick={cleanNow}
              title={dirty ? 'Save first: this uses the saved numbers' : 'Delete what is old enough, now'}
            >
              Delete old files now
            </button>
            {message && <span className="sp-ok" role="status">{message}</span>}
          </div>
        )}

        {last && (
          <p className="sp-note">
            Last cleanup {new Date(last.at).toLocaleString()}: {last.alerts_deleted} alert(s),{' '}
            {last.recordings_deleted} recording(s)
            {last.errors?.length ? `, ${last.errors.length} problem(s)` : ''}.
          </p>
        )}
      </section>
    </div>
  )
}

export default StorageSettings
