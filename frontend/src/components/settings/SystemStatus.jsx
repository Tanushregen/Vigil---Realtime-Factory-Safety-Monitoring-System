import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import './SettingsPanels.css'

const LABEL = { ok: 'Working', warn: 'Check', error: 'Problem', off: 'Off' }
const OVERALL = {
  ok: 'Everything is working',
  warn: 'Working, with something to check',
  error: 'Something needs fixing',
}

function Dot({ state }) {
  return <span className={`sp-dot ${state}`} title={LABEL[state]} />
}

function SystemStatus() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/system/status')
      if (!response.ok) throw new Error('Unable to load the status')
      setStatus(await response.json())
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [load])

  return (
    <div className="sp-stack">
      {error && <p className="error-message">{error}</p>}

      {status && (
        <section className={`sp-banner ${status.overall}`}>
          <Dot state={status.overall} />
          <div>
            <strong>{OVERALL[status.overall]}</strong>
            <small>
              {status.app.commit ? `Version ${status.app.commit} · ` : ''}up {status.app.uptime} · times in{' '}
              {status.app.time_zone}
            </small>
          </div>
          <button type="button" className="sp-link" onClick={load} disabled={loading}>
            <RefreshCw size={14} /> {loading ? 'Checking...' : 'Check again'}
          </button>
        </section>
      )}

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Services</h2>
            <p>What VIGIL depends on. Keys are never shown here.</p>
          </div>
        </div>
        <ul className="sp-checks">
          {(status?.checks || []).map(check => (
            <li key={check.id}>
              <Dot state={check.state} />
              <div>
                <strong>{check.label}</strong>
                <small>{check.detail}</small>
              </div>
              <span className={`sp-state ${check.state}`}>{LABEL[check.state]}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Cameras</h2>
            <p>Stream and analysis state of each camera</p>
          </div>
        </div>
        {status && status.cameras.length === 0 ? (
          <p className="sp-note">No cameras yet.</p>
        ) : (
          <div className="ov-table-wrap">
            <table className="ov-table">
              <thead>
                <tr>
                  <th>Camera</th>
                  <th>Stream</th>
                  <th>Analysis</th>
                  <th>Zones</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(status?.cameras || []).map(camera => (
                  <tr key={camera.id}>
                    <td>{camera.name}</td>
                    <td>{camera.stream}</td>
                    <td>{camera.analysis_enabled ? (camera.running ? 'Running' : 'Not running') : 'Off'}</td>
                    <td>{camera.zones}</td>
                    <td>
                      <Dot state={camera.state} /> {camera.detail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

export default SystemStatus
