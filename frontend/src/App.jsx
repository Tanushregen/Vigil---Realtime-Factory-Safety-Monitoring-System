
import { useEffect, useState } from 'react'
import AddDvr from './pages/AddDvr'
import CameraDetail from './pages/CameraDetail'
import SafetyRules from './pages/SafetyRules'
import Settings from './pages/Settings'
import Overview from './pages/Overview'
import './App.css'

const zones = [
  'All',
  'Shop floor',
  'Press area',
  'Gate',
  'Storage',
  'Fire exit',
  'Office',
  'Other',
]

function App() {
  const [cameras, setCameras] = useState([])
  const [selectedZone, setSelectedZone] = useState('All')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [thumbnailTime, setThumbnailTime] = useState(Date.now())
  const [currentPage, setCurrentPage] = useState('cameras')
  const [selectedCamera, setSelectedCamera] = useState(null)

  // Fetch cameras from backend
  async function fetchCameras(zone = selectedZone) {
    try {
      const url =
        zone === 'All'
          ? '/api/cameras'
          : `/api/cameras?zone=${encodeURIComponent(zone)}`

      const response = await fetch(url)

      if (!response.ok) {
        throw new Error('Unable to load cameras')
      }

      const data = await response.json()

      setCameras(data)
      setError('')
    } catch (err) {
      setError(
        'Cannot connect to the backend. Check that FastAPI is running.'
      )
    } finally {
      setLoading(false)
    }
  }

  // Fetch cameras whenever selected zone changes
  useEffect(() => {
    setLoading(true)
    fetchCameras(selectedZone)

    const cameraInterval = setInterval(() => {
      fetchCameras(selectedZone)
    }, 10000)

    const thumbnailInterval = setInterval(() => {
      setThumbnailTime(Date.now())
    }, 30000)

    return () => {
      clearInterval(cameraInterval)
      clearInterval(thumbnailInterval)
    }
  }, [selectedZone])

  // Camera counts
  const filteredCameras = cameras

  const onlineCount = cameras.filter(
    camera => camera.status === 'online'
  ).length

  const offlineCount = cameras.filter(
    camera => camera.status === 'offline'
  ).length

  // Update camera details
  function handleCameraUpdate(updatedCamera) {
    setCameras(previous =>
      previous.map(camera =>
        camera.id === updatedCamera.id
          ? updatedCamera
          : camera
      )
    )

    setSelectedCamera(updatedCamera)
  }

  // Delete camera from dashboard
  function handleCameraDelete(cameraId) {
    setCameras(previous =>
      previous.filter(camera => camera.id !== cameraId)
    )

    setSelectedCamera(null)
    setCurrentPage('cameras')
  }

  // Sidebar navigation
  function navigateTo(page) {
    setCurrentPage(page)
    setSelectedCamera(null)
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h2 className="logo">VIGIL</h2>
        <p className="sidebar-label">FACTORY MONITORING</p>

        <div
            className={`nav-item ${currentPage === 'overview' ? 'active' : ''}`}
            onClick={() => navigateTo('overview')}
        >
            Overview
        </div>

        <div
          className={`nav-item ${
            currentPage === 'cameras' ? 'active' : ''
          }`}
          onClick={() => navigateTo('cameras')}
        >
          Cameras
        </div>

        <div
          className={`nav-item ${
            currentPage === 'add-dvr' ? 'active' : ''
          }`}
          onClick={() => navigateTo('add-dvr')}
        >
          Add DVR / NVR
        </div>

        <div
          className={`nav-item ${
            currentPage === 'safety-rules' ? 'active' : ''
          }`}
          onClick={() => navigateTo('safety-rules')}
        >
          Safety Rules
        </div>

        <div
          className={`nav-item ${
            currentPage === 'alerts' ? 'active' : ''
          }`}
          onClick={() => navigateTo('alerts')}
        >
          Alerts
        </div>

        <div
          className={`nav-item ${
            currentPage === 'settings' ? 'active' : ''
          }`}
          onClick={() => navigateTo('settings')}
        >
          Settings
        </div>
      </aside>

      <main className="main-content">

        {/* Add DVR page */}
        {currentPage === 'overview' ? (
          <Overview />
        ) : currentPage === 'add-dvr' ? (
          <AddDvr />

        /* Safety Rules page */
        ) : currentPage === 'safety-rules' ? (
          <SafetyRules />

        /* Alerts page */
        ) : currentPage === 'alerts' ? (
          <div className="placeholder-page">
            <h1>Alerts</h1>
          </div>

        /* Settings page */
        ) : currentPage === 'settings' ? (
          <Settings />

        /* Camera details page */
        ) : selectedCamera ? (
          <CameraDetail
            camera={selectedCamera}
            onBack={() => setSelectedCamera(null)}
            onDeleted={handleCameraDelete}
            onUpdated={handleCameraUpdate}
          />

        /* Camera dashboard */
        ) : (
          <>
            <header className="topbar">
              <div>
                <h1>Cameras</h1>
                <p className="subtitle">
                  Monitor and manage your factory cameras
                </p>
              </div>

              <button
                className="refresh-button"
                onClick={() => {
                  setLoading(true)
                  fetchCameras(selectedZone)
                }}
              >
                Refresh
              </button>
            </header>

            <section className="stats">
              <div className="stat-card">
                <p>Total Cameras</p>
                <h2>{cameras.length}</h2>
              </div>

              <div className="stat-card">
                <p>Online</p>
                <h2 className="online-text">
                  {onlineCount}
                </h2>
              </div>

              <div className="stat-card">
                <p>Offline</p>
                <h2 className="offline-text">
                  {offlineCount}
                </h2>
              </div>
            </section>

            <section className="camera-section">
              <div className="section-heading">
                <h2>Camera List</h2>
                <span>{filteredCameras.length} cameras</span>
              </div>

              <div className="filters">
                {zones.map(zone => (
                  <button
                    key={zone}
                    className={`filter-button ${
                      selectedZone === zone ? 'selected' : ''
                    }`}
                    onClick={() => setSelectedZone(zone)}
                  >
                    {zone}
                  </button>
                ))}
              </div>

              {error && (
                <p className="error-message">{error}</p>
              )}

              {loading ? (
                <p className="empty-message">
                  Loading cameras...
                </p>
              ) : filteredCameras.length === 0 ? (
                <div className="empty-state">
                  <h3>No cameras found</h3>
                  <p>
                    Add a DVR or camera to start monitoring
                    your factory.
                  </p>
                </div>
              ) : (
                <div className="camera-grid">
                  {filteredCameras.map(camera => (
                    <div
                      className="camera-card"
                      key={camera.id}
                      onClick={() => setSelectedCamera(camera)}
                    >
                      <div className="camera-image">
                        <img
                          src={`http://127.0.0.1:1984/api/frame.jpeg?src=cam${camera.id}&t=${thumbnailTime}`}
                          alt={camera.name}
                          onError={event => {
                            event.currentTarget.style.display = 'none'
                            event.currentTarget.parentElement.classList.add(
                              'no-image'
                            )
                          }}
                        />

                        <span
                          className={`status ${
                            camera.status === 'online'
                              ? 'online'
                              : camera.status === 'offline'
                                ? 'offline'
                                : 'unknown'
                          }`}
                        >
                          {camera.status || 'Unknown'}
                        </span>
                      </div>

                      <div className="camera-info">
                        <h3>{camera.name}</h3>
                        <p>
                          {camera.zone || 'No zone assigned'}
                        </p>
                        <span>
                          Channel {camera.channel}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  )
}

export default App
