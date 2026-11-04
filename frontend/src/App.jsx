
import { useEffect, useState } from 'react'
import {
  ArrowLeft,
  Bell,
  Building2,
  Cctv,
  ChevronRight,
  CircleCheck,
  Cog,
  DoorOpen,
  Ellipsis,
  Factory,
  Flame,
  House,
  Layers,
  LogOut,
  Menu,
  ScanEye,
  Gauge,
  Settings as SettingsIcon,
  ServerCog,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  UserRound,
  Users as UsersIcon,
  Video,
  VideoOff,
  X,
} from 'lucide-react'
import AddDvr from './pages/AddDvr'
import Alerts from './pages/Alerts'
import CameraDetail from './pages/CameraDetail'
import SafetyRules from './pages/SafetyRules'
import Settings from './pages/Settings'
import Overview from './pages/Overview'
import SceneLog from './pages/SceneLog'
import Metrics from './pages/Metrics'
import Users from './pages/Users'
import PageHeader from './components/PageHeader'
import DeleteGroupDialog from './components/DeleteGroupDialog'
import { useAuth } from './auth/authContext'
import logo from './assets/vigil-logo-light.png'
import './App.css'
import './pages/Cameras.css'

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

const zoneIcons = {
  'Shop floor': Factory,
  'Press area': Cog,
  Gate: DoorOpen,
  Storage: Layers,
  'Fire exit': Flame,
  Office: Building2,
  Other: Ellipsis,
}

const navItems = [
  { page: 'overview', label: 'Overview', icon: House },
  { page: 'cameras', label: 'Cameras', icon: Video },
  { page: 'add-dvr', label: 'Add DVR / NVR', icon: ServerCog },
  { page: 'safety-rules', label: 'Safety Rules', icon: ShieldCheck },
  { page: 'alerts', label: 'Alerts', icon: Bell },
  { page: 'scene', label: 'Scene Log', icon: ScanEye },
  { page: 'metrics', label: 'Metrics', icon: Gauge },
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

// One wide card per camera group: a 2x2 preview on the left, the group on the right
function GroupCard({ group, members, thumbnailTime, onOpen }) {
  const onlineCount = members.filter(c => c.status === 'online').length
  const state =
    onlineCount === members.length
      ? 'online'
      : onlineCount === 0
        ? 'offline'
        : 'partial'

  const badge =
    state === 'online'
      ? 'All Online'
      : state === 'offline'
        ? 'All Offline'
        : `${onlineCount} of ${members.length} Online`

  return (
    <button className="cm-group" onClick={() => onOpen(group.id)}>
      <div className="cm-group-grid">
        {members.slice(0, 4).map(camera => (
          <div className="cm-tile" key={camera.id}>
            <img
              src={cameraThumbnail(camera.id, thumbnailTime)}
              alt={camera.name}
              onError={hideBrokenThumbnail}
            />

            <span className="cm-tile-label">
              <i
                className={
                  camera.status === 'online'
                    ? 'on'
                    : camera.status === 'offline'
                      ? 'off'
                      : ''
                }
              />
              {camera.name}
            </span>
          </div>
        ))}
      </div>

      <div className="cm-group-info">
        <span className="ov-icon purple">
          <Building2 size={28} strokeWidth={1.8} />
        </span>

        <div>
          <h3>{group.name}</h3>
          <p>
            {members.length} camera{members.length === 1 ? '' : 's'}
          </p>
          <span className={`cm-badge ${state}`}>
            {state === 'offline' ? (
              <TriangleAlert size={18} />
            ) : (
              <CircleCheck size={18} />
            )}
            {badge}
          </span>
        </div>
      </div>

      <span className="cm-group-arrow" aria-hidden="true">
        <ChevronRight size={22} />
      </span>
    </button>
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
  const [deletingGroup, setDeletingGroup] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleteError, setDeleteError] = useState('')

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

  // Delete the open group, with or without its cameras
  async function handleGroupDelete(deleteCameras) {
    if (!selectedGroup) return

    setDeletingGroup(true)
    setDeleteError('')

    try {
      const response = await fetch(
        `/api/groups/${selectedGroup.id}?delete_cameras=${deleteCameras}`,
        { method: 'DELETE' }
      )
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail || 'Unable to delete the group')
      }

      setConfirmingDelete(false)
      setSelectedGroupId(null)
      await fetchCameras()
    } catch (err) {
      setDeleteError(err.message)
    } finally {
      setDeletingGroup(false)
    }
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
        className="main-content main-art"
      >
        <div className="main-content-inner">
        {currentPage === 'overview' ? (
          <Overview onOpenAlerts={() => navigateTo('alerts')} />
        ) : currentPage === 'add-dvr' ? (
          <AddDvr onOpenAlerts={() => navigateTo('alerts')} />
        ) : currentPage === 'safety-rules' ? (
          <SafetyRules onOpenAlerts={() => navigateTo('alerts')} />
        ) : currentPage === 'alerts' ? (
          <Alerts />
        ) : currentPage === 'scene' ? (
          <SceneLog onOpenAlerts={() => navigateTo('alerts')} />
        ) : currentPage === 'metrics' ? (
          <Metrics onOpenAlerts={() => navigateTo('alerts')} />
        ) : currentPage === 'settings' ? (
          <Settings onOpenAlerts={() => navigateTo('alerts')} />
        ) : currentPage === 'users' && isAdmin ? (
          <Users onOpenAlerts={() => navigateTo('alerts')} />
        ) : selectedCamera ? (
          <CameraDetail
            camera={selectedCamera}
            onBack={() => setSelectedCamera(null)}
            onDeleted={handleCameraDelete}
            onUpdated={handleCameraUpdate}
            onOpenAlerts={() => navigateTo('alerts')}
          />
        ) : selectedGroup ? (
          <div className="ov-page">
            <PageHeader
              icon={Building2}
              title={selectedGroup.name}
              subtitle={`${selectedGroupCameras.length} camera${
                selectedGroupCameras.length === 1 ? '' : 's'
              } in this group`}
              crumbs={['VIGIL', 'Cameras', selectedGroup.name]}
              onOpenAlerts={() => navigateTo('alerts')}
              onRefresh={() => fetchCameras()}
            />

            <div className="cm-toolbar">
              <button
                className="cm-back"
                onClick={() => setSelectedGroupId(null)}
              >
                <ArrowLeft size={18} /> Back to Cameras
              </button>

              <button
                className="cm-delete"
                onClick={() => {
                  setDeleteError('')
                  setConfirmingDelete(true)
                }}
              >
                <Trash2 size={17} /> Delete group
              </button>
            </div>

            {confirmingDelete && (
              <DeleteGroupDialog
                groupName={selectedGroup.name}
                cameraNames={selectedGroupCameras.map(camera => camera.name)}
                busy={deletingGroup}
                error={deleteError}
                onCancel={() => setConfirmingDelete(false)}
                onConfirm={handleGroupDelete}
              />
            )}

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
          </div>
        ) : (
          <div className="ov-page">
            <PageHeader
              icon={Video}
              title="Cameras"
              subtitle="Monitor and manage your factory cameras"
              crumbs={['VIGIL', 'Cameras']}
              onOpenAlerts={() => navigateTo('alerts')}
              onRefresh={() => {
                setLoading(true)
                fetchCameras()
              }}
              refreshing={loading && cameras.length > 0}
            />

            <section className="ov-stats three">
              <article className="ov-card compact">
                <div className="ov-card-head">
                  <span className="ov-icon blue">
                    <Video size={24} />
                  </span>
                  <h3>Total Cameras</h3>
                </div>
                <strong className="ov-card-value">
                  {filteredCameras.length}
                </strong>
                <Cctv className="ov-card-art blue" size={64} strokeWidth={1.4} />
              </article>

              <article className="ov-card compact">
                <div className="ov-card-head">
                  <span className="ov-icon green">
                    <CircleCheck size={24} />
                  </span>
                  <h3>Online</h3>
                  <span className="ov-dot green" />
                </div>
                <strong className="ov-card-value green">{onlineCount}</strong>
                <Cctv className="ov-card-art green" size={64} strokeWidth={1.4} />
              </article>

              <article className="ov-card compact">
                <div className="ov-card-head">
                  <span className="ov-icon red">
                    <TriangleAlert size={24} />
                  </span>
                  <h3>Offline</h3>
                  <span className="ov-dot red" />
                </div>
                <strong className="ov-card-value red">{offlineCount}</strong>
                <VideoOff
                  className="ov-card-art red"
                  size={64}
                  strokeWidth={1.4}
                />
              </article>
            </section>

            <section className="cm-section">
              <div className="cm-section-head">
                <h2>Camera List</h2>
                <span>
                  {filteredCameras.length} camera
                  {filteredCameras.length === 1 ? '' : 's'}
                </span>
              </div>

              <div className="cm-filters">
                {zones.map(zone => {
                  const ZoneIcon = zoneIcons[zone]

                  return (
                    <button
                      key={zone}
                      className={selectedZone === zone ? 'selected' : ''}
                      onClick={() => setSelectedZone(zone)}
                    >
                      {ZoneIcon && <ZoneIcon size={18} />}
                      {zone}
                    </button>
                  )
                })}
              </div>

              {error && <p className="error-message">{error}</p>}

              {loading && cameras.length === 0 ? (
                <p className="empty-message">Loading cameras...</p>
              ) : filteredCameras.length === 0 ? (
                <div className="empty-state">
                  <h3>No cameras found</h3>
                  <p>
                    {selectedZone === 'All'
                      ? 'Add a DVR or camera to start monitoring your factory.'
                      : `No cameras are assigned to ${selectedZone}.`}
                  </p>
                </div>
              ) : (
                <>
                  {groupsWithMembers.length > 0 && (
                    <>
                      <h2 className="cm-subtitle">Camera Groups</h2>

                      <div className="cm-groups">
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
                      <h2 className="cm-subtitle">
                        {groupsWithMembers.length > 0
                          ? 'Ungrouped Cameras'
                          : 'Cameras'}
                      </h2>

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
          </div>
        )}
        </div>
      </main>
    </div>
  )
}

export default App
