import { useState } from 'react'
import {
  CirclePlay,
  Eye,
  EyeOff,
  Info,
  Layers,
  LayoutGrid,
  Link as LinkIcon,
  LoaderCircle,
  Lock,
  MapPin,
  Network,
  Server,
  ServerCog,
  User,
  Video,
} from 'lucide-react'
import PageHeader from '../components/PageHeader'
import './AddDvr.css'

// Label with an icon in the left gutter; the control sits under the label
function Field({ icon: Icon, label, wide = false, children }) {
  return (
    <label className={`ac-field ${wide ? 'wide' : ''}`}>
      <Icon className="ac-field-icon" size={22} strokeWidth={1.8} />
      <span className="ac-label">{label}</span>
      {children}
    </label>
  )
}

function AddDvr({ onOpenAlerts }) {
  const [mode, setMode] = useState('dvr')
  const [showPassword, setShowPassword] = useState(false)

  // DVR form
  const [formData, setFormData] = useState({
    name: '',
    brand: 'Hikvision',
    ip: '',
    port: 554,
    username: '',
    password: '',
    channel_count: 4,
  })

  // Manual camera form
  const [manualData, setManualData] = useState({
    name: '',
    rtsp_url: '',
    zone: '',
  })

  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [results, setResults] = useState([])

  function handleChange(event) {
    const { name, value } = event.target

    setFormData((previous) => ({
      ...previous,
      [name]:
        name === 'port' || name === 'channel_count'
          ? Number(value)
          : value,
    }))
  }

  function handleManualChange(event) {
    const { name, value } = event.target

    setManualData((previous) => ({
      ...previous,
      [name]: value,
    }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setLoading(true)
    setMessage('')
    setResults([])

    try {
      const response = await fetch('/api/dvrs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData),
      })

      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail || 'Failed to add DVR')
      }

      setMessage('DVR processing completed successfully.')
      setResults(data.cameras || [])
      setFormData((previous) => ({
        ...previous,
        password: '',
      }))
    } catch (error) {
      setMessage(error.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleManualSubmit(event) {
    event.preventDefault()
    setLoading(true)
    setMessage('')
    setResults([])

    try {
      const response = await fetch('/api/cameras/manual', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(manualData),
      })

      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail || 'Failed to add camera')
      }

      setMessage(data.message || 'Camera added successfully.')
      setResults([data.camera])
      setManualData({
        name: '',
        rtsp_url: '',
        zone: '',
      })
    } catch (error) {
      setMessage(error.message)
    } finally {
      setLoading(false)
    }
  }

  function switchMode(nextMode) {
    setMode(nextMode)
    setMessage('')
    setResults([])
  }

  return (
    <div className="ov-page ac-page">
      <PageHeader
        icon={mode === 'dvr' ? ServerCog : Video}
        title="Add Camera"
        subtitle="Connect a factory DVR/NVR or add an individual RTSP camera."
        crumbs={['VIGIL', 'Add DVR / NVR']}
        onOpenAlerts={onOpenAlerts}
      />

      <div className="ac-modes" role="tablist" aria-label="Connection type">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'dvr'}
          className={mode === 'dvr' ? 'active' : ''}
          onClick={() => switchMode('dvr')}
        >
          <Server size={22} />
          Add DVR / NVR
        </button>

        <button
          type="button"
          role="tab"
          aria-selected={mode === 'manual'}
          className={mode === 'manual' ? 'active' : ''}
          onClick={() => switchMode('manual')}
        >
          <Video size={22} />
          Manual RTSP Camera
        </button>
      </div>

      {mode === 'dvr' && (
        <form onSubmit={handleSubmit} className="ac-form">
          <Field icon={Server} label="DVR Name">
            <input
              name="name"
              value={formData.name}
              onChange={handleChange}
              placeholder="Main Factory DVR"
              required
            />
          </Field>

          <Field icon={Layers} label="Brand">
            <select
              name="brand"
              value={formData.brand}
              onChange={handleChange}
            >
              <option value="Hikvision">Hikvision</option>
              <option value="Dahua">Dahua</option>
              <option value="CP Plus">CP Plus</option>
            </select>
          </Field>

          <Field icon={Network} label="IP Address">
            <input
              name="ip"
              value={formData.ip}
              onChange={handleChange}
              placeholder="192.168.1.100"
              required
            />
          </Field>

          <Field icon={LinkIcon} label="RTSP Port">
            <input
              type="number"
              name="port"
              min="1"
              max="65535"
              value={formData.port}
              onChange={handleChange}
              required
            />
          </Field>

          <Field icon={User} label="Username">
            <input
              name="username"
              value={formData.username}
              onChange={handleChange}
              placeholder="Enter username"
              autoComplete="off"
              required
            />
          </Field>

          <Field icon={Lock} label="Password">
            <span className="ac-password">
              <input
                type={showPassword ? 'text' : 'password'}
                name="password"
                value={formData.password}
                onChange={handleChange}
                placeholder="Enter password"
                autoComplete="off"
                required
              />
              <button
                type="button"
                className="ac-eye"
                onClick={() => setShowPassword(shown => !shown)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff size={20} /> : <Eye size={20} />}
              </button>
            </span>
          </Field>

          <Field icon={LayoutGrid} label="Number of Channels">
            <input
              type="number"
              name="channel_count"
              min="1"
              max="256"
              value={formData.channel_count}
              onChange={handleChange}
              required
            />
          </Field>

          <Field icon={Video} label="Stream Type">
            <select value="sub" disabled aria-readonly="true">
              <option value="sub">Substream (recommended)</option>
            </select>
          </Field>

          <button type="submit" className="ac-submit" disabled={loading}>
            {loading ? (
              <LoaderCircle size={26} className="ac-spin" />
            ) : (
              <CirclePlay size={26} strokeWidth={1.8} />
            )}
            {loading ? 'Checking Channels...' : 'Check Channels'}
          </button>
        </form>
      )}

      {mode === 'manual' && (
        <form onSubmit={handleManualSubmit} className="ac-form">
          <Field icon={Video} label="Camera Name">
            <input
              name="name"
              value={manualData.name}
              onChange={handleManualChange}
              placeholder="Camera 01"
              required
            />
          </Field>

          <Field icon={MapPin} label="Zone">
            <select
              name="zone"
              value={manualData.zone}
              onChange={handleManualChange}
            >
              <option value="">Select Zone</option>
              <option value="Shop floor">Shop floor</option>
              <option value="Press area">Press area</option>
              <option value="Gate">Gate</option>
              <option value="Storage">Storage</option>
              <option value="Fire exit">Fire exit</option>
              <option value="Office">Office</option>
              <option value="Other">Other</option>
            </select>
          </Field>

          <Field icon={LinkIcon} label="RTSP URL" wide>
            <input
              type="text"
              name="rtsp_url"
              value={manualData.rtsp_url}
              onChange={handleManualChange}
              placeholder="rtsp://user:pass@ip:554/path  (also rtsps, rtmp, http MJPEG/HLS, srt)"
              autoComplete="off"
              required
            />
          </Field>

          <button type="submit" className="ac-submit" disabled={loading}>
            {loading ? (
              <LoaderCircle size={26} className="ac-spin" />
            ) : (
              <CirclePlay size={26} strokeWidth={1.8} />
            )}
            {loading ? 'Validating Camera...' : 'Add Camera'}
          </button>
        </form>
      )}

      {message && (
        <p className="ac-message" role="status">
          <Info size={20} />
          {message}
        </p>
      )}

      {results.length > 0 && (
        <div className="ac-results">
          <h2>{mode === 'dvr' ? 'Channel Results' : 'Camera Details'}</h2>

          {results.map((camera, index) => (
            <div key={camera.id || camera.channel || index} className="ac-result-row">
              <span>
                {mode === 'dvr'
                  ? `Channel ${camera.channel}`
                  : camera.name}
              </span>
              <span className={`ac-status ${camera.status || 'unknown'}`}>
                {camera.status || 'Unknown'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default AddDvr
