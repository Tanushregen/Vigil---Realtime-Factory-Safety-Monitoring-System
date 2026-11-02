
import { useEffect, useState } from 'react'

function Overview() {
  const [cameras, setCameras] = useState([])
  const [dvrs, setDvrs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function fetchOverview() {
    try {
      const [cameraResponse, dvrResponse] = await Promise.all([
        fetch('/api/cameras'),
        fetch('/api/dvrs')
      ])

      if (!cameraResponse.ok || !dvrResponse.ok) {
        throw new Error('Unable to fetch dashboard data')
      }

      const cameraData = await cameraResponse.json()
      const dvrData = await dvrResponse.json()

      setCameras(cameraData)
      setDvrs(dvrData)
      setError('')
    } catch (err) {
      setError('Unable to load overview. Check the backend connection.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchOverview()

    const interval = setInterval(fetchOverview, 10000)

    return () => clearInterval(interval)
  }, [])

  const onlineCount = cameras.filter(
    camera => camera.status === 'online'
  ).length

  const offlineCount = cameras.filter(
    camera => camera.status === 'offline'
  ).length

  const unknownCount = cameras.length - onlineCount - offlineCount

  if (loading) {
    return <p className="empty-message">Loading overview...</p>
  }

  return (
    <div className="overview-page">
      <header className="topbar">
        <div>
          <h1>Overview</h1>
          <p className="subtitle">
            Factory monitoring system summary
          </p>
        </div>

        <button className="refresh-button" onClick={() => {
          setLoading(true)
          fetchOverview()
        }}>
          Refresh
        </button>
      </header>

      {error && <p className="error-message">{error}</p>}

      <section className="stats">
        <div className="stat-card">
          <p>Total Cameras</p>
          <h2>{cameras.length}</h2>
        </div>

        <div className="stat-card">
          <p>Online Cameras</p>
          <h2 className="online-text">{onlineCount}</h2>
        </div>

        <div className="stat-card">
          <p>Offline Cameras</p>
          <h2 className="offline-text">{offlineCount}</h2>
        </div>

        <div className="stat-card">
          <p>Total DVR / NVR</p>
          <h2>{dvrs.length}</h2>
        </div>
      </section>

      <section className="camera-section">
        <div className="section-heading">
          <h2>Camera Status Summary</h2>
          <span>{cameras.length} cameras</span>
        </div>

        <div className="stats">
          <div className="stat-card">
            <p>Online</p>
            <h2 className="online-text">{onlineCount}</h2>
          </div>

          <div className="stat-card">
            <p>Offline</p>
            <h2 className="offline-text">{offlineCount}</h2>
          </div>

          <div className="stat-card">
            <p>Unknown</p>
            <h2>{unknownCount}</h2>
          </div>
        </div>
      </section>

      <section className="camera-section">
        <div className="section-heading">
          <h2>Registered Recorders</h2>
          <span>{dvrs.length} recorders</span>
        </div>

        {dvrs.length === 0 ? (
          <p className="empty-message">No DVR / NVR devices registered.</p>
        ) : (
          <div className="camera-grid">
            {dvrs.map(dvr => (
              <div className="camera-card" key={dvr.id}>
                <div className="camera-info">
                  <h3>{dvr.name}</h3>
                  <p>{dvr.brand}</p>
                  <span>IP: {dvr.ip}</span>
                  <p>Port: {dvr.port}</p>
                  <p>Channels: {dvr.channel_count}</p>
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
