
import { useEffect, useState } from 'react'
import {
  Bell,
  ChevronRight,
  House,
  LogOut,
  Menu,
  Settings as SettingsIcon,
  ServerCog,
  ShieldCheck,
  UserRound,
  Users as UsersIcon,
  Video,
  X,
} from 'lucide-react'
import AddDvr from './pages/AddDvr'
import Alerts from './pages/Alerts'
import CameraDetail from './pages/CameraDetail'
import SafetyRules from './pages/SafetyRules'
import Settings from './pages/Settings'
import Overview from './pages/Overview'
import Users from './pages/Users'
import { useAuth } from './auth/authContext'
import logo from './assets/vigil-logo-light.png'
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

const navItems = [
  { page: 'overview', label: 'Overview', icon: House },
  { page: 'cameras', label: 'Cameras', icon: Video },
  { page: 'add-dvr', label: 'Add DVR / NVR', icon: ServerCog },
  { page: 'safety-rules', label: 'Safety Rules', icon: ShieldCheck },
  { page: 'alerts', label: 'Alerts', icon: Bell },
  { page: 'settings', label: 'Settings', icon: SettingsIcon },
  { page: 'users', label: 'Users', icon: UsersIcon, adminOnly: true },
]

function cameraThumbnail(cameraId, thumbnailTime) {
  return `/go2rtc/api/frame.jpeg?src=cam${cameraId}&t=${thumbnailTime}`
}

function hideBrokenThumbnail(event) {
  event.currentTarget.style.display = 'none'
  event.currentTarget.parentElement.classList.add('no-image')
}

function CameraCard({ camera, thumbnailTime, onOpen }) {
  return (
    <div className="camera-card" onClick={() => onOpen(camera)}>
      <div className="camera-image">
        <img
          src={cameraThumbnail(camera.id, thumbnailTime)}
          alt={camera.name}
          onError={hideBrokenThumbnail}
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
        <p>{camera.zone || 'No zone assigned'}</p>
        <span>Channel {camera.channel}</span>
      </div>
    </div>
  )
}

// One profile card per camera group, with a 2x2 preview of its cameras
function GroupCard({ group, members, thumbnailTime, onOpen }) {
  const onlineCount = members.filter(c => c.status === 'online').length
  const state =
    onlineCount === members.length
      ? 'online'
      : onlineCount === 0
        ? 'offline'
        : 'partial'

  return (
    <div className="camera-card group-card" onClick={() => onOpen(group.id)}>
      <div className="group-mosaic">
        {members.slice(0, 4).map(camera => (
          <div className="camera-image" key={camera.id}>
            <img
              src={cameraThumbnail(camera.id, thumbnailTime)}
              alt={camera.name}
              onError={hideBrokenThumbnail}
            />
          </div>
        ))}
      </div>

      <div className="camera-info">
        <h3>{group.name}</h3>
        <p>
          {members.length} camera{members.length === 1 ? '' : 's'}
        </p>
        <span className={`group-status ${state}`}>
          {onlineCount} of {members.length} online
        </span>
      </div>
    </div>
  )
}

function App() {
  const { user, isAdmin, logout } = useAuth()
  const [cameras, setCameras] = useState([])
  const [groups, setGroups] = useState([])
  const [selectedZone, setSelectedZone] = useState('All')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [thumbnailTime, setThumbnailTime] = useState(Date.now())
  const [currentPage, setCurrentPage] = useState('cameras')
  const [selectedCamera, setSelectedCamera] = useState(null)
  const [selectedGroupId, setSelectedGroupId] = useState(null)
  const [menuOpen, setMenuOpen] = useState(false)

  // Fetch every camera and every group from the backend
  async function fetchCameras() {
    try {
      const [cameraResponse, groupResponse] = await Promise.all([
        fetch('/api/cameras'),
        fetch('/api/groups'),
      ])

      if (!cameraResponse.ok) {
        throw new Error('Unable to load cameras')
      }

      setCameras(await cameraResponse.json())

      // Groups are optional: the camera list still works without them
      if (groupResponse.ok) {
        setGroups(await groupResponse.json())
      }

      setError('')
    } catch (err) {
      setError(
        'Cannot connect to the backend. Check that FastAPI is running.'
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchCameras()

    const cameraInterval = setInterval(fetchCameras, 10000)

    const thumbnailInterval = setInterval(() => {
      setThumbnailTime(Date.now())
    }, 30000)

    return () => {
      clearInterval(cameraInterval)
      clearInterval(thumbnailInterval)
    }
  }, [])

  // The zone filter applies to the cameras shown on the dashboard
  const filteredCameras =
    selectedZone === 'All'
      ? cameras
      : cameras.filter(camera => camera.zone === selectedZone)

  const onlineCount = filteredCameras.filter(
    camera => camera.status === 'online'
  ).length

  const offlineCount = filteredCameras.filter(
    camera => camera.status === 'offline'
  ).length

  // Groups with their cameras, limited to the cameras passing the zone filter
  const cameraById = new Map(cameras.map(camera => [camera.id, camera]))
  const visibleIds = new Set(filteredCameras.map(camera => camera.id))

  const groupsWithMembers = groups
    .map(group => ({
      group,
      members: group.cameraIds
        .filter(id => visibleIds.has(id))
        .map(id => cameraById.get(id)),
    }))
    .filter(entry => entry.members.length > 0)

  const groupedIds = new Set(groups.flatMap(group => group.cameraIds))
  const ungroupedCameras = filteredCameras.filter(
    camera => !groupedIds.has(camera.id)
  )

  const selectedGroup = groups.find(group => group.id === selectedGroupId)
  const selectedGroupCameras = selectedGroup
    ? selectedGroup.cameraIds
        .map(id => cameraById.get(id))
        .filter(Boolean)
    : []

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
    setSelectedGroupId(null)
    setMenuOpen(false)
  }

  return (
    <div className="app">
      {/* Mobile top bar with the menu button */}
      <div className="mobile-bar">
        <button
          className="mobile-menu-button"
          aria-label="Open menu"
          onClick={() => setMenuOpen(true)}
        >
          <Menu size={22} />
        </button>
        <img src={logo} alt="VIGIL" className="mobile-bar-logo" />
      </div>

      {menuOpen && (
        <div className="sidebar-backdrop" onClick={() => setMenuOpen(false)} />
      )}

      {/* Fixed sidebar */}
      <aside className={`sidebar ${menuOpen ? 'open' : ''}`}>
        <button
          className="sidebar-close"
          aria-label="Close menu"
          onClick={() => setMenuOpen(false)}
        >
          <X size={20} />
        </button>

        <div className="sidebar-brand">
          <img src={logo} alt="VIGIL" className="sidebar-logo" />
          <p className="sidebar-label">FACTORY MONITORING</p>
        </div>

        <nav className="sidebar-nav">
          {navItems
            .filter(item => !item.adminOnly || isAdmin)
            .map(({ page, label, icon: Icon }) => (
              <button
                key={page}
                className={`nav-item ${currentPage === page ? 'active' : ''}`}
                aria-current={currentPage === page ? 'page' : undefined}
                onClick={() => navigateTo(page)}
              >
                <Icon size={20} strokeWidth={1.9} />
                <span>{label}</span>
              </button>
            ))}
        </nav>

        <div className="sidebar-user">
          <div className="sidebar-avatar">
            <UserRound size={22} strokeWidth={1.8} />
          </div>

          <div className="sidebar-user-text">
            <p className="sidebar-user-name">{user.name}</p>
            <span className="sidebar-user-role">{user.role}</span>
          </div>

          <button
            className="logout-button"
            onClick={logout}
            title="Log out"
            aria-label="Log out"
          >
            <LogOut size={18} />
            <ChevronRight size={16} />
          </button>
        </div>
      </aside>

      {/* Independently scrollable main content */}
      <main
        className={`main-content ${
          currentPage === 'overview' ? 'main-overview' : ''
        }`}
      >
        <div className="main-content-inner">
        {currentPage === 'overview' ? (
          <Overview onOpenAlerts={() => navigateTo('alerts')} />
        ) : currentPage === 'add-dvr' ? (
          <AddDvr />
        ) : currentPage === 'safety-rules' ? (
          <SafetyRules />
        ) : currentPage === 'alerts' ? (
          <Alerts />
        ) : currentPage === 'settings' ? (
          <Settings />
        ) : currentPage === 'users' && isAdmin ? (
          <Users />
        ) : selectedCamera ? (
          <CameraDetail
            camera={selectedCamera}
            onBack={() => setSelectedCamera(null)}
            onDeleted={handleCameraDelete}
            onUpdated={handleCameraUpdate}
          />
        ) : selectedGroup ? (
          <>
            <button
              className="back-button"
              onClick={() => setSelectedGroupId(null)}
            >
              ← Back to Cameras
            </button>

            <header className="topbar">
              <div>
                <h1>{selectedGroup.name}</h1>
                <p className="subtitle">
                  {selectedGroupCameras.length} camera
                  {selectedGroupCameras.length === 1 ? '' : 's'} in this group
                </p>
              </div>

              <button className="refresh-button" onClick={fetchCameras}>
                Refresh
              </button>
            </header>

            {selectedGroupCameras.length === 0 ? (
              <div className="empty-state">
                <h3>No cameras in this group</h3>
                <p>Add cameras to the group from Safety Rules.</p>
              </div>
            ) : (
              <div className="camera-grid">
                {selectedGroupCameras.map(camera => (
                  <CameraCard
                    key={camera.id}
                    camera={camera}
                    thumbnailTime={thumbnailTime}
                    onOpen={setSelectedCamera}
                  />
                ))}
              </div>
            )}
          </>
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
                  fetchCameras()
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
                <h2 className="online-text">{onlineCount}</h2>
              </div>

              <div className="stat-card">
                <p>Offline</p>
                <h2 className="offline-text">{offlineCount}</h2>
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
                <p className="empty-message">Loading cameras...</p>
              ) : filteredCameras.length === 0 ? (
                <div className="empty-state">
                  <h3>No cameras found</h3>
                  <p>
                    Add a DVR or camera to start monitoring
                    your factory.
                  </p>
                </div>
              ) : (
                <>
                  {groupsWithMembers.length > 0 && (
                    <>
                      <h3 className="subsection-title">Camera Groups</h3>

                      <div className="camera-grid">
                        {groupsWithMembers.map(({ group, members }) => (
                          <GroupCard
                            key={group.id}
                            group={group}
                            members={members}
                            thumbnailTime={thumbnailTime}
                            onOpen={setSelectedGroupId}
                          />
                        ))}
                      </div>
                    </>
                  )}

                  {ungroupedCameras.length > 0 && (
                    <>
                      {groupsWithMembers.length > 0 && (
                        <h3 className="subsection-title">
                          Ungrouped Cameras
                        </h3>
                      )}

                      <div className="camera-grid">
                        {ungroupedCameras.map(camera => (
                          <CameraCard
                            key={camera.id}
                            camera={camera}
                            thumbnailTime={thumbnailTime}
                            onOpen={setSelectedCamera}
                          />
                        ))}
                      </div>
                    </>
                  )}
                </>
              )}
            </section>
          </>
        )}
        </div>
      </main>
    </div>
  )
}

export default App
