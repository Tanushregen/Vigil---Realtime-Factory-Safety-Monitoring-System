
import { useEffect, useState } from 'react'


function Overview() {
  const [cameras, setCameras] = useState([])
  const [dvrs, setDvrs] = useState([])
  const [healthHistory, setHealthHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  async function fetchOverview(isManual = false) {
    try {
      if (isManual) setRefreshing(true)

      const [cameraResponse, dvrResponse, historyResponse] = await Promise.all([
          fetch('/api/cameras'),
          fetch('/api/dvrs'),
          fetch('/api/cameras/health-history?days=7')
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
  const lastSevenDays = Array.from({ length: 7 }, (_, index) => {
  const date = new Date()
  date.setHours(0, 0, 0, 0)
  date.setDate(date.getDate() - (6 - index))

  const dateKey = date.toLocaleDateString('en-CA')

  const dayHistory = healthHistory.filter(item =>
    new Date(item.recorded_at).toLocaleDateString('en-CA') === dateKey
  )

  return {
    date: dateKey,
    label: date.toLocaleDateString('en-IN', {
      weekday: 'short'
    }),
    online: dayHistory.filter(item => item.status === 'online').length,
    offline: dayHistory.filter(item => item.status === 'offline').length
  }
})
  const operationalPercent = total ? (onlineCount / total) * 100 : 0
  const warningPercent = total ? (unknownCount / total) * 100 : 0
  const criticalPercent = total ? (offlineCount / total) * 100 : 0

  const donutStyle = {
    background: total
      ? `conic-gradient(
          #10b981 0% ${operationalPercent}%,
          #f59e0b ${operationalPercent}% ${operationalPercent + warningPercent}%,
          #ef4444 ${operationalPercent + warningPercent}% 100%
        )`
      : '#e5e7eb'
  }

  if (loading) {
    return (
      <div className="overview-loading">
        <div className="loading-spinner" />
        <p>Loading factory overview...</p>
      </div>
    )
  }

  return (
    <div className="overview-page">

      {/* Page Header */}
      <header className="overview-header">
        <div>
          <div className="overview-breadcrumb">VIGIL / Dashboard</div>
          <h1>Factory Overview</h1>
          <p>Monitor your factory surveillance and camera infrastructure.</p>
        </div>

        <button
          className="overview-refresh"
          onClick={() => fetchOverview(true)}
          disabled={refreshing}
        >
          <span className={refreshing ? 'refresh-icon spinning' : 'refresh-icon'}>
            ↻
          </span>
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </header>

      {error && (
        <div className="overview-error">
          {error}
          <button onClick={() => fetchOverview(true)}>Retry</button>
        </div>
      )}

      {/* Summary Cards */}
      <section className="overview-stats">

        <div className="overview-stat-card">
          <div className="overview-stat-top">
            <div className="overview-stat-icon blue-icon">▣</div>
            <span className="overview-stat-label">Infrastructure</span>
          </div>
          <p>Total Cameras</p>
          <h2>{total}</h2>
          <span className="overview-stat-note">Registered cameras</span>
        </div>

        <div className="overview-stat-card">
          <div className="overview-stat-top">
            <div className="overview-stat-icon green-icon">✓</div>
            <span className="overview-status-dot green-dot" />
          </div>
          <p>Online Cameras</p>
          <h2 className="green-text">{onlineCount}</h2>
          <span className="overview-stat-note">Operational cameras</span>
        </div>

        <div className="overview-stat-card">
          <div className="overview-stat-top">
            <div className="overview-stat-icon red-icon">!</div>
            <span className="overview-status-dot red-dot" />
          </div>
          <p>Offline Cameras</p>
          <h2 className="red-text">{offlineCount}</h2>
          <span className="overview-stat-note">Disconnected cameras</span>
        </div>

        <div className="overview-stat-card">
          <div className="overview-stat-top">
            <div className="overview-stat-icon purple-icon">▤</div>
            <span className="overview-stat-label">Infrastructure</span>
          </div>
          <p>Registered DVR / NVR</p>
          <h2>{dvrs.length}</h2>
          <span className="overview-stat-note">Connected recorders registered</span>
        </div>

      </section>

      {/* Analytics */}
      <section className="overview-analytics">

        <div className="overview-panel">
          <div className="overview-panel-header">
            <div>
              <h2>Camera Status Distribution</h2>
              <p>Current camera health breakdown</p>
            </div>
            <span className="overview-live-label">LIVE STATUS</span>
          </div>

          <div className="donut-layout">
            <div className="donut-chart" style={donutStyle}>
              <div className="donut-hole">
                <strong>{total}</strong>
                <span>Total Cameras</span>
              </div>
            </div>

            <div className="donut-legend">
              <div>
                <span className="legend-dot operational" />
                <div>
                  <strong>Operational</strong>
                  <small>{onlineCount} cameras</small>
                </div>
                <b>{operationalPercent.toFixed(0)}%</b>
              </div>

              <div>
                <span className="legend-dot warning" />
                <div>
                  <strong>Unknown</strong>
                  <small>{unknownCount} cameras</small>
                </div>
                <b>{warningPercent.toFixed(0)}%</b>
              </div>

              <div>
                <span className="legend-dot critical" />
                <div>
                  <strong>Critical</strong>
                  <small>{offlineCount} cameras</small>
                </div>
                <b>{criticalPercent.toFixed(0)}%</b>
              </div>
            </div>
          </div>
        </div>

        <div className="overview-panel">
          <div className="overview-panel-header">
            <div>
              <h2>Camera Health Overview</h2>
              <p>Current infrastructure status</p>
            </div>
          </div>

          <div className="health-summary">
            <div className="health-summary-item">
              <span>Online</span>
              <strong className="green-text">{onlineCount}</strong>
            </div>
            <div className="health-summary-item">
              <span>Offline</span>
              <strong className="red-text">{offlineCount}</strong>
            </div>
            <div className="health-summary-item">
              <span>Unknown</span>
              <strong>{unknownCount}</strong>
            </div>
          </div>

          <div className="health-bar-list">
            {[
              { label: 'Operational', value: onlineCount, color: '#10b981' },
              { label: 'Unknown', value: unknownCount, color: '#f59e0b' },
              { label: 'Offline', value: offlineCount, color: '#ef4444' }
            ].map(item => (
              <div className="health-bar-item" key={item.label}>
                <div className="health-bar-label">
                  <span>{item.label}</span>
                  <strong>{item.value}</strong>
                </div>
                <div className="health-bar-track">
                  <div
                    className="health-bar-fill"
                    style={{
                      width: `${total ? (item.value / total) * 100 : 0}%`,
                      background: item.color
                    }}
                  />
                </div>
              </div>
            ))}
          </div>

          <p className="health-footnote">
            Based on the latest camera connectivity status. 
          </p>
        </div>

      </section>
      
{/* 7-Day Camera Activity */}
<section className="overview-panel history-panel">
  <div className="overview-panel-header">
    <div>
      <h2>7-Day Camera Activity</h2>
      <p>Daily online and offline status changes</p>
    </div>
    <span className="recorder-count">Last 7 Days</span>
  </div>

  {healthHistory.length === 0 ? (
    <div className="overview-empty">
      <h3>No historical activity yet</h3>
      <p>Camera status changes will appear here when recorded.</p>
    </div>
  ) : (
    <div className="history-chart">
      {lastSevenDays.map(day => {
        const maxValue = Math.max(
          1,
          ...lastSevenDays.map(d => d.online + d.offline)
        )

        return (
          <div className="history-day" key={day.date}>
            <div className="history-bars">
              <div
                className="history-bar online-history-bar"
                style={{
                  height: `${(day.online / maxValue) * 100}%`
                }}
                title={`Online changes: ${day.online}`}
              />
              <div
                className="history-bar offline-history-bar"
                style={{
                  height: `${(day.offline / maxValue) * 100}%`
                }}
                title={`Offline changes: ${day.offline}`}
              />
            </div>
            <span>{day.label}</span>
          </div>
        )
      })}
    </div>
  )}

  <div className="history-legend">
    <span><i className="legend-dot operational" /> Online changes</span>
    <span><i className="legend-dot critical" /> Offline changes</span>
  </div>
</section>

{/* Recent Activity Logs */}
<section className="overview-panel history-panel">
  <div className="overview-panel-header">
    <div>
      <h2>Recent Activity Logs</h2>
      <p>Latest camera connectivity changes</p>
    </div>
    <span className="recorder-count">
      {healthHistory.length} Records
    </span>
  </div>

  {healthHistory.length === 0 ? (
    <div className="overview-empty">
      <h3>No recent activity</h3>
      <p>Camera connectivity events will appear here.</p>
    </div>
  ) : (
    <div className="activity-table-wrapper">
      <table className="activity-table">
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
              <td>
                {new Date(item.recorded_at).toLocaleString()}
              </td>
              <td>{item.camera_name}</td>
              <td>
                {item.status === 'online'
                  ? 'Camera connected'
                  : 'Camera disconnected'}
              </td>
              <td>{item.zone || 'Not assigned'}</td>
              <td>
                <span className={`activity-status ${item.status}`}>
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
      <section className="overview-panel recorder-panel">
        <div className="overview-panel-header">
          <div>
            <h2>Registered DVR / NVR Devices</h2>
            <p>Manage your registered recording devices</p>
          </div>
          <span className="recorder-count">{dvrs.length} Devices</span>
        </div>

        {dvrs.length === 0 ? (
          <div className="overview-empty">
            <div className="empty-icon">▣</div>
            <h3>No recording devices yet</h3>
            <p>Registered DVR / NVR devices will appear here.</p>
          </div>
        ) : (
          <div className="recorder-grid">
            {dvrs.map(dvr => (
              <div className="recorder-card" key={dvr.id}>
                <div className="recorder-card-icon">▣</div>
                <div className="recorder-card-info">
                  <h3>{dvr.name}</h3>
                  <p>{dvr.brand || 'Recorder'}</p>
                </div>
                <div className="recorder-details">
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
