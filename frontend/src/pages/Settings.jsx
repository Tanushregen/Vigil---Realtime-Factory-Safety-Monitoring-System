import { useEffect, useState } from 'react'
import { MessageCircle, Server, Settings as SettingsIcon, Video } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import './Metrics.css'

// WhatsApp alerts: is it on, who gets the messages, and a button to try it
function Notifications() {
  const [info, setInfo] = useState(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch('/api/notifications/status')
      .then(response => (response.ok ? response.json() : null))
      .then(setInfo)
      .catch(() => setInfo(null))
  }, [])

  async function call(path, okText) {
    setBusy(true)
    setMessage('')
    try {
      const response = await fetch(path, { method: 'POST' })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Request failed')
      const failed = (data.results || []).filter(item => !item.ok)
      setMessage(failed.length ? `Failed: ${failed[0].error}` : data.status === 'failed'
        ? `Failed: ${(data.errors || []).join('; ')}` : okText)
    } catch (err) {
      setMessage(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="ov-panel">
      <div className="ov-panel-head">
        <div>
          <h2>WhatsApp alerts</h2>
          <p>
            Each alert is sent with its clip. A daily report goes out once a day.
          </p>
        </div>
        <span className={`ov-pill ${info?.enabled ? '' : 'off'}`}>
          {info?.enabled ? 'On' : 'Off'}
        </span>
      </div>

      <div style={{ padding: '0 20px 20px', display: 'grid', gap: 10 }}>
        {info && !info.enabled && (
          <p>
            Not set up yet: {info.reason}. Add the settings to the server's .env
            file and restart.
          </p>
        )}
        {info?.enabled && (
          <>
            <p>
              Sending to {info.recipients.join(', ')}.{' '}
              {info.use_templates
                ? 'Using the approved message template.'
                : 'Falls back to the approved template outside the 24 hour window.'}
            </p>
            <p>
              Daily report:{' '}
              {info.report_hour == null
                ? 'off'
                : `every day at ${String(info.report_hour).padStart(2, '0')}:00 (${info.timezone})`}
            </p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button
                type="button"
                className="mt-submit"
                disabled={busy}
                onClick={() => call('/api/notifications/test', 'Test message sent.')}
              >
                Send a test message
              </button>
              <button
                type="button"
                className="mt-submit"
                disabled={busy}
                onClick={() => call('/api/reports/daily/send', 'The report was sent.')}
              >
                Send today&apos;s report now
              </button>
            </div>
          </>
        )}
        {message && <p role="status">{message}</p>}
      </div>
    </section>
  )
}

function Settings({ onOpenAlerts }) {
  const [activeTab, setActiveTab] = useState('cameras')

  return (
    <div className="ov-page">
      <PageHeader
        icon={SettingsIcon}
        title="Settings"
        subtitle="Manage VIGIL configuration."
        crumbs={['VIGIL', 'Settings']}
        onOpenAlerts={onOpenAlerts}
      />

      <div className="cm-filters" role="tablist" aria-label="Settings sections">
        <button
          role="tab"
          aria-selected={activeTab === 'cameras'}
          className={activeTab === 'cameras' ? 'selected' : ''}
          onClick={() => setActiveTab('cameras')}
        >
          <Video size={18} />
          Cameras
        </button>

        <button
          role="tab"
          aria-selected={activeTab === 'recorders'}
          className={activeTab === 'recorders' ? 'selected' : ''}
          onClick={() => setActiveTab('recorders')}
        >
          <Server size={18} />
          Recorders
        </button>

        <button
          role="tab"
          aria-selected={activeTab === 'notifications'}
          className={activeTab === 'notifications' ? 'selected' : ''}
          onClick={() => setActiveTab('notifications')}
        >
          <MessageCircle size={18} />
          Notifications
        </button>
      </div>

      {activeTab === 'cameras' && (
        <section className="ov-panel">
          <div className="ov-panel-head">
            <div>
              <h2>Camera Settings</h2>
              <p>Camera configuration settings.</p>
            </div>
          </div>
        </section>
      )}

      {activeTab === 'notifications' && <Notifications />}

      {activeTab === 'recorders' && (
        <section className="ov-panel">
          <div className="ov-panel-head">
            <div>
              <h2>Recorder Settings</h2>
              <p>DVR/NVR configuration settings.</p>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

export default Settings
