import { useEffect, useState } from 'react'
import {
  Bell,
  Cctv,
  CircleCheck,
  CircleHelp,
  Factory,
  House,
  RefreshCw,
  Server,
  TriangleAlert,
  Video,
  VideoOff,
} from 'lucide-react'
import './Overview.css'

const STATUS_COLORS = {
  online: '#10b981',
  unknown: '#f59e0b',
  offline: '#ef4444',
}

const RING_RADIUS = 78
const RING_LENGTH = 2 * Math.PI * RING_RADIUS

// Donut chart drawn as stacked SVG arcs; it follows the counts it is given
function StatusDonut({ segments, total }) {
  let used = 0

  return (
    <div className="ov-donut">
      <svg viewBox="0 0 200 200" role="img" aria-label="Camera status chart">
        <circle
          className="ov-donut-track"
          cx="100"
          cy="100"
          r={RING_RADIUS}
        />

        {total > 0 &&
          segments
            .filter(segment => segment.value > 0)
            .map(segment => {
              const length = (segment.value / total) * RING_LENGTH
              const offset = -used
              used += length

              return (
                <circle
                  key={segment.key}
                  className="ov-donut-arc"
                  cx="100"
                  cy="100"
                  r={RING_RADIUS}
                  stroke={segment.color}
                  strokeDasharray={`${length} ${RING_LENGTH - length}`}
                  strokeDashoffset={offset}
                />
              )
            })}
      </svg>

      <div className="ov-donut-center">
        <strong>{total}</strong>
        <span>Total Cameras</span>
      </div>
    </div>
  )
}

function Overview({ onOpenAlerts }) {
  const [cameras, setCameras] = useState([])
  const [dvrs, setDvrs] = useState([])
  const [healthHistory, setHealthHistory] = useState([])
  const [recentAlerts, setRecentAlerts] = useState(0)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  async function fetchOverview(isManual = false) {
    try {
      if (isManual) setRefreshing(true)

      const [cameraResponse, dvrResponse, historyResponse] = await Promise.all([
        fetch('/api/cameras'),
        fetch('/api/dvrs'),
        fetch('/api/cameras/health-history?days=7'),
      ])

      if (!cameraResponse.ok || !dvrResponse.ok || !historyResponse.ok) {
        throw new Error('Unable to fetch dashboard data')
      }

      const cameraData = await cameraResponse.json()
      const dvrData = await dvrResponse.json()
      const historyData = await historyResponse.json()

      setCameras(Array.isArray(cameraData) ? cameraData : [])
      setDvrs(Array.isArray(dvrData) ? dvrData : [])
      setHealthHistory(Array.isArray(historyData) ? historyData : [])
      setError('')

      // The alert badge is optional: a failure here must not break the page
      fetch('/api/violations?since_hours=24&limit=1')
        .then(response => (response.ok ? response.json() : null))
        .then(data => setRecentAlerts(data?.total || 0))
        .catch(() => {})
    } catch (err) {
      console.error('Overview fetch error:', err)
      setError(err.message || 'Unable to load overview.')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    fetchOverview()

    const interval = setInterval(() => fetchOverview(), 10000)

    return () => clearInterval(interval)
  }, [])

  const onlineCount = cameras.filter(
    camera => camera.status?.toLowerCase() === 'online'
  ).length

  const offlineCount = cameras.filter(
    camera => camera.status?.toLowerCase() === 'offline'
  ).length

  const unknownCount = cameras.length - onlineCount - offlineCount

  const total = cameras.length

  const percentOf = value => (total ? Math.round((value / total) * 100) : 0)

  const statusRows = [
    { key: 'online', label: 'Operational', value: onlineCount },
    { key: 'unknown', label: 'Unknown', value: unknownCount },
    { key: 'offline', label: 'Offline', value: offlineCount },
  ].map(row => ({ ...row, color: STATUS_COLORS[row.key] }))

  const lastSevenDays = Array.from({ length: 7 }, (_, index) => {
    const date = new Date()
    date.setHours(0, 0, 0, 0)
    date.setDate(date.getDate() - (6 - index))

    const dateKey = date.toLocaleDateString('en-CA')

    const dayHistory = healthHistory.filter(
      item =>
        new Date(item.recorded_at).toLocaleDateString('en-CA') === dateKey
    )

    return {
      date: dateKey,
      label: date.toLocaleDateString('en-IN', { weekday: 'short' }),
      online: dayHistory.filter(item => item.status === 'online').length,
      offline: dayHistory.filter(item => item.status === 'offline').length,
    }
  })

  const maxHistoryValue = Math.max(
    1,
    ...lastSevenDays.map(day => day.online + day.offline)
  )

  if (loading) {
    return (
      <div className="ov-loading">
        <div className="ov-spinner" />
        <p>Loading factory overview...</p>
      </div>
    )
  }

  return (
    <div className="ov-page">
      {/* Breadcrumb and notifications */}
      <div className="ov-topline">
        <nav className="ov-breadcrumb" aria-label="Breadcrumb">
          <House size={18} />
          <span>VIGIL</span>
          <i>/</i>
          <strong>Dashboard</strong>
        </nav>

        <button
          className="ov-bell"
          onClick={onOpenAlerts}
          aria-label={
            recentAlerts
              ? `${recentAlerts} alerts in the last 24 hours`
              : 'Alerts'
          }
          title={
            recentAlerts
              ? `${recentAlerts} alerts in the last 24 hours`
              : 'No alerts in the last 24 hours'
          }
        >
          <Bell size={21} />
          {recentAlerts > 0 && <span className="ov-bell-dot" />}
        </button>
      </div>

      {/* Page header */}
      <header className="ov-header">
        <div className="ov-header-icon">
          <Factory size={34} strokeWidth={1.7} />
        </div>

        <div className="ov-header-text">
          <h1>Factory Overview</h1>
          <p>Monitor your factory surveillance and camera infrastructure.</p>
        </div>

        <button
          className="ov-refresh"
          onClick={() => fetchOverview(true)}
          disabled={refreshing}
        >
          <RefreshCw size={19} className={refreshing ? 'ov-spin' : ''} />
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </header>

      {error && (
        <div className="ov-error">
          <span>{error}</span>
          <button onClick={() => fetchOverview(true)}>Retry</button>
        </div>
      )}

      {/* KPI cards */}
      <section className="ov-stats">
        <article className="ov-card">
          <div className="ov-card-head">
            <span className="ov-icon blue">
              <Video size={24} />
            </span>
            <h3>Infrastructure</h3>
          </div>
          <p className="ov-card-label">Total Cameras</p>
          <strong className="ov-card-value">{total}</strong>
          <span className="ov-card-note">Registered cameras</span>
          <Cctv className="ov-card-art blue" size={64} strokeWidth={1.4} />
        </article>

        <article className="ov-card">
          <div className="ov-card-head">
            <span className="ov-icon green">
              <CircleCheck size={24} />
            </span>
            <h3>Online</h3>
            <span className="ov-dot green" />
          </div>
          <p className="ov-card-label">Online Cameras</p>
          <strong className="ov-card-value green">{onlineCount}</strong>
          <span className="ov-card-note">Operational cameras</span>
        </article>

        <article className="ov-card">
          <div className="ov-card-head">
            <span className="ov-icon red">
              <TriangleAlert size={24} />
            </span>
            <h3>Offline</h3>
            <span className="ov-dot red" />
          </div>
          <p className="ov-card-label">Offline Cameras</p>
          <strong className="ov-card-value red">{offlineCount}</strong>
          <span className="ov-card-note">Disconnected cameras</span>
          <VideoOff className="ov-card-art red" size={64} strokeWidth={1.4} />
        </article>

        <article className="ov-card">
          <div className="ov-card-head">
            <span className="ov-icon purple">
              <Server size={24} />
            </span>
            <h3>Infrastructure</h3>
          </div>
          <p className="ov-card-label">Registered DVR / NVR</p>
          <strong className="ov-card-value">{dvrs.length}</strong>
          <span className="ov-card-note">Connected recorders registered</span>
          <Server className="ov-card-art purple" size={64} strokeWidth={1.4} />
        </article>
      </section>

      {/* Analytics */}
      <section className="ov-analytics">
        <div className="ov-panel">
          <div className="ov-panel-head">
            <div>
              <h2>Camera Status Distribution</h2>
              <p>Current camera health breakdown</p>
            </div>
            <span className="ov-live">
              <i /> LIVE STATUS
            </span>
          </div>

          <div className="ov-donut-layout">
            <StatusDonut segments={statusRows} total={total} />

            <ul className="ov-legend">
              {statusRows.map(row => (
                <li key={row.key}>
                  <i style={{ background: row.color }} />
                  <div>
                    <strong>{row.label}</strong>
                    <small>
                      {row.value} camera{row.value === 1 ? '' : 's'}
                    </small>
                  </div>
                  <b>{percentOf(row.value)}%</b>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="ov-panel">
          <div className="ov-panel-head">
            <div>
              <h2>Camera Health Overview</h2>
              <p>Current infrastructure status</p>
            </div>
          </div>

          <div className="ov-health">
            <div className="ov-health-card green">
              <span className="ov-icon green">
                <Video size={24} />
              </span>
              <p>Online</p>
              <strong>{onlineCount}</strong>
            </div>

            <div className="ov-health-card red">
              <span className="ov-icon red">
                <VideoOff size={24} />
              </span>
              <p>Offline</p>
              <strong>{offlineCount}</strong>
            </div>

            <div className="ov-health-card neutral">
              <span className="ov-icon neutral">
                <CircleHelp size={24} />
              </span>
              <p>Unknown</p>
              <strong>{unknownCount}</strong>
            </div>
          </div>

          <p className="ov-footnote">
            Based on the latest camera connectivity status.
          </p>
        </div>
      </section>

      {/* 7-day camera activity */}
      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>7-Day Camera Activity</h2>
            <p>Daily online and offline status changes</p>
          </div>
          <span className="ov-pill">Last 7 Days</span>
        </div>

        {healthHistory.length === 0 ? (
          <div className="ov-empty">
            <h3>No historical activity yet</h3>
            <p>Camera status changes will appear here when recorded.</p>
          </div>
        ) : (
          <div className="ov-history">
            {lastSevenDays.map(day => (
              <div className="ov-history-day" key={day.date}>
                <div className="ov-history-bars">
                  <div
                    className="ov-bar online"
                    style={{ height: `${(day.online / maxHistoryValue) * 100}%` }}
                    title={`Online changes: ${day.online}`}
                  />
                  <div
                    className="ov-bar offline"
                    style={{
                      height: `${(day.offline / maxHistoryValue) * 100}%`,
                    }}
                    title={`Offline changes: ${day.offline}`}
                  />
                </div>
                <span>{day.label}</span>
              </div>
            ))}
          </div>
        )}

        <div className="ov-history-legend">
          <span>
            <i style={{ background: STATUS_COLORS.online }} /> Online changes
          </span>
          <span>
            <i style={{ background: STATUS_COLORS.offline }} /> Offline changes
          </span>
        </div>
      </section>

      {/* Recent activity */}
      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Recent Activity Logs</h2>
            <p>Latest camera connectivity changes</p>
          </div>
          <span className="ov-pill">{healthHistory.length} Records</span>
        </div>

        {healthHistory.length === 0 ? (
          <div className="ov-empty">
            <h3>No recent activity</h3>
            <p>Camera connectivity events will appear here.</p>
          </div>
        ) : (
          <div className="ov-table-wrap">
            <table className="ov-table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Camera Name</th>
                  <th>Activity</th>
                  <th>Location</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {healthHistory.slice(0, 10).map(item => (
                  <tr key={item.id}>
                    <td>{new Date(item.recorded_at).toLocaleString()}</td>
                    <td>{item.camera_name}</td>
                    <td>
                      {item.status === 'online'
                        ? 'Camera connected'
                        : 'Camera disconnected'}
                    </td>
                    <td>{item.zone || 'Not assigned'}</td>
                    <td>
                      <span className={`ov-status ${item.status}`}>
                        {item.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* DVR / NVR */}
      <section className="ov-panel">
        <div className="ov-panel-head">
          <div>
            <h2>Registered DVR / NVR Devices</h2>
            <p>Manage your registered recording devices</p>
          </div>
          <span className="ov-pill">{dvrs.length} Devices</span>
        </div>

        {dvrs.length === 0 ? (
          <div className="ov-empty">
            <span className="ov-icon purple">
              <Server size={24} />
            </span>
            <h3>No recording devices yet</h3>
            <p>Registered DVR / NVR devices will appear here.</p>
          </div>
        ) : (
          <div className="ov-recorders">
            {dvrs.map(dvr => (
              <div className="ov-recorder" key={dvr.id}>
                <span className="ov-icon purple">
                  <Server size={24} />
                </span>
                <div className="ov-recorder-info">
                  <h3>{dvr.name}</h3>
                  <p>{dvr.brand || 'Recorder'}</p>
                </div>
                <div className="ov-recorder-details">
                  <span>IP Address</span>
                  <strong>{dvr.ip}</strong>
                  <span>Port: {dvr.port}</span>
                  <span>Channels: {dvr.channel_count}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

export default Overview
