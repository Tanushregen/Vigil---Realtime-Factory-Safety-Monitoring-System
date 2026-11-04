
import { useState } from 'react'

function AddDvr() {
  const [mode, setMode] = useState('dvr')

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

  return (
    <div className="form-page">
      <h1>Add Camera</h1>
      <p>Connect a factory DVR/NVR or add an individual RTSP camera.</p>

      <div className="form-tabs">
        <button
          type="button"
          className={mode === 'dvr' ? 'active' : ''}
          onClick={() => {
            setMode('dvr')
            setMessage('')
            setResults([])
          }}
        >
          Add DVR / NVR
        </button>

        <button
          type="button"
          className={mode === 'manual' ? 'active' : ''}
          onClick={() => {
            setMode('manual')
            setMessage('')
            setResults([])
          }}
        >
          Manual RTSP Camera
        </button>
      </div>

      {mode === 'dvr' && (
        <form onSubmit={handleSubmit} className="dvr-form">
          <label>
            DVR Name
            <input
              name="name"
              value={formData.name}
              onChange={handleChange}
              placeholder="Main Factory DVR"
              required
            />
          </label>

          <label>
            Brand
            <select
              name="brand"
              value={formData.brand}
              onChange={handleChange}
            >
              <option value="Hikvision">Hikvision</option>
              <option value="Dahua">Dahua</option>
              <option value="CP Plus">CP Plus</option>
            </select>
          </label>

          <label>
            IP Address
            <input
              name="ip"
              value={formData.ip}
              onChange={handleChange}
              placeholder="192.168.1.100"
              required
            />
          </label>

          <label>
            RTSP Port
            <input
              type="number"
              name="port"
              min="1"
              max="65535"
              value={formData.port}
              onChange={handleChange}
              required
            />
          </label>

          <label>
            Username
            <input
              name="username"
              value={formData.username}
              onChange={handleChange}
              required
            />
          </label>

          <label>
            Password
            <input
              type="password"
              name="password"
              value={formData.password}
              onChange={handleChange}
              required
            />
          </label>

          <label>
            Number of Channels
            <input
              type="number"
              name="channel_count"
              min="1"
              max="256"
              value={formData.channel_count}
              onChange={handleChange}
              required
            />
          </label>

          <label>
            Stream Type
            <input value="Substream (recommended)" disabled />
          </label>

          <button type="submit" disabled={loading}>
            {loading ? 'Checking Channels...' : 'Check Channels'}
          </button>
        </form>
      )}

      {mode === 'manual' && (
        <form onSubmit={handleManualSubmit} className="dvr-form">
          <label>
            Camera Name
            <input
              name="name"
              value={manualData.name}
              onChange={handleManualChange}
              placeholder="Camera 01"
              required
            />
          </label>

          <label>
            RTSP URL
            <input
              type="text"
              name="rtsp_url"
              value={manualData.rtsp_url}
              onChange={handleManualChange}
              placeholder="rtsp://user:pass@ip:554/path  (also rtsps, rtmp, http MJPEG/HLS, srt)"
              autoComplete="off"
              required
            />
          </label>

          <label>
            Zone
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
          </label>

          <button type="submit" disabled={loading}>
            {loading ? 'Validating Camera...' : 'Add Camera'}
          </button>
        </form>
      )}

      {message && <p className="form-message">{message}</p>}

      {results.length > 0 && (
        <div className="channel-results">
          <h2>{mode === 'dvr' ? 'Channel Results' : 'Camera Details'}</h2>

          {results.map((camera, index) => (
            <div key={camera.id || camera.channel || index} className="channel-row">
              <span>
                {mode === 'dvr'
                  ? `Channel ${camera.channel}`
                  : camera.name}
              </span>
              <span className={`status ${camera.status || 'unknown'}`}>
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
