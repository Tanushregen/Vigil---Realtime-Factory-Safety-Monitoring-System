import { useCallback, useEffect, useState } from 'react'
import { Gauge } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import './Metrics.css'

const RANGES = [
  { hours: 24, label: 'Last 24 hours' },
  { hours: 24 * 7, label: 'Last 7 days' },
  { hours: 24 * 30, label: 'Last 30 days' },
]

const percent = value => (value == null ? '-' : `${Math.round(value * 100)}%`)
const seconds = ms => (ms == null ? '-' : `${(ms / 1000).toFixed(1)} s`)
const money = value => (value == null ? '-' : `$${value.toFixed(value < 1 ? 3 : 2)}`)

function Stat({ label, value, note }) {
  return (
    <article className="ov-card mt-stat">
      <p className="ov-card-label">{label}</p>
      <strong className="ov-card-value">{value}</strong>
      <span className="ov-card-note">{note}</span>
    </article>
  )
}

function Metrics({ onOpenAlerts }) {
  const [hours, setHours] = useState(24)
  const [data, setData] = useState(null)
  const [cameras, setCameras] = useState([])
  const [error, setError] = useState('')
  const [form, setForm] = useState({ camera_id: '', violation_type: '', note: '' })
  const [saved, setSaved] = useState('')

  const load = useCallback(async () => {
    try {
      const [metricsResponse, camerasResponse] = await Promise.all([
        fetch(`/api/metrics?since_hours=${hours}`),
        fetch('/api/cameras'),
      ])
      if (!metricsResponse.ok) throw new Error('Unable to load the metrics')
      setData(await metricsResponse.json())
      if (camerasResponse.ok) setCameras(await camerasResponse.json())
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [hours])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [load])

  async function reportMissed(event) {
    event.preventDefault()
    setSaved('')
    try {
      const response = await fetch('/api/missed-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          camera_id: Number(form.camera_id),
          violation_type: form.violation_type || null,
          note: form.note || null,
        }),
      })
      if (!response.ok) throw new Error('Could not save the missed event')
      setForm({ camera_id: form.camera_id, violation_type: '', note: '' })
      setSaved('Saved. Recall is updated.')
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  const totals = data?.totals
  const rows = data?.cameras || []

  return (
    <div className="ov-page mt-page">
      <PageHeader
        icon={Gauge}
        title="Metrics"
        subtitle="How accurate, fast and costly VIGIL is on each camera"
        crumbs={['VIGIL', 'Metrics']}
        onOpenAlerts={onOpenAlerts}
      />

      {error && <p className="error-message">{error}</p>}

      <div className="mt-toolbar">
        <select value={hours} onChange={event => setHours(Number(event.target.value))}>
          {RANGES.map(range => (
            <option key={range.hours} value={range.hours}>
              {range.label}
            </option>
          ))}
        </select>
        <span>
          Precision and false alerts come from the Yes / No answers on the Alerts
          page. Recall comes from the missed events you report below.
        </span>
      </div>

      {totals && (
        <section className="ov-stats">
          <Stat
            label="Precision"
            value={percent(totals.precision)}
            note={`${totals.reviewed} of ${totals.alerts} alerts reviewed`}
          />
          <Stat
            label="Recall"
            value={percent(totals.recall)}
            note={`${totals.missed} missed event${totals.missed === 1 ? '' : 's'} reported`}
          />
          <Stat
            label="False alerts"
            value={totals.false_alerts}
            note="marked as not a real violation"
          />
          <Stat
            label="Model cost"
            value={money(totals.cost_usd)}
            note={
              data.cost_prices_set
                ? `${(totals.prompt_tokens + totals.completion_tokens).toLocaleString()} tokens`
                : 'set META_PRICE_IN_PER_M / META_PRICE_OUT_PER_M'
            }
          />
        </section>
      )}

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Per camera</h2>
            <p>A dash means there is nothing to calculate it from yet</p>
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="ov-empty">
            <h3>No data yet</h3>
            <p>Numbers appear once a camera has been scanned or has raised an alert.</p>
          </div>
        ) : (
          <div className="ov-table-wrap">
            <table className="ov-table">
              <thead>
                <tr>
                  <th>Camera</th>
                  <th>Alerts</th>
                  <th>Precision</th>
                  <th>Recall</th>
                  <th>False alerts / day</th>
                  <th>Alert latency (p50 / p95)</th>
                  <th>Scans</th>
                  <th>Avg scan</th>
                  <th>Idle scans saved</th>
                  <th>Cost / day</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.camera_id}>
                    <td>{row.camera_name || `Camera ${row.camera_id}`}</td>
                    <td>
                      {row.alerts}
                      {row.unreviewed > 0 && (
                        <small className="mt-muted"> ({row.unreviewed} unreviewed)</small>
                      )}
                    </td>
                    <td>{percent(row.precision)}</td>
                    <td>{percent(row.recall)}</td>
                    <td>{row.false_alerts_per_day ?? '-'}</td>
                    <td>
                      {seconds(row.alert_latency_p50_ms)} / {seconds(row.alert_latency_p95_ms)}
                    </td>
                    <td>
                      {row.scans}
                      {row.scan_failures > 0 && (
                        <small className="mt-muted"> ({row.scan_failures} failed)</small>
                      )}
                    </td>
                    <td>{seconds(row.scan_latency_avg_ms)}</td>
                    <td>{percent(row.motion_gating_saved)}</td>
                    <td>{money(row.cost_per_day_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Report a missed event</h2>
            <p>A violation you saw that VIGIL did not alert on</p>
          </div>
        </div>

        <form className="mt-form" onSubmit={reportMissed}>
          <label>
            Camera
            <select
              value={form.camera_id}
              onChange={event => setForm({ ...form, camera_id: event.target.value })}
              required
            >
              <option value="">Choose a camera</option>
              {cameras.map(camera => (
                <option key={camera.id} value={camera.id}>
                  {camera.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            What was missed
            <input
              value={form.violation_type}
              onChange={event => setForm({ ...form, violation_type: event.target.value })}
              placeholder="e.g. no helmet"
              maxLength={80}
            />
          </label>
          <label>
            Note
            <input
              value={form.note}
              onChange={event => setForm({ ...form, note: event.target.value })}
              placeholder="optional"
              maxLength={300}
            />
          </label>
          <button type="submit" className="mt-submit">
            Report missed event
          </button>
        </form>
        {saved && <p className="mt-saved">{saved}</p>}
      </section>
    </div>
  )
}

export default Metrics
