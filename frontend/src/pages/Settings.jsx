import { useCallback, useEffect, useState } from 'react'
import { Activity, HardDrive, MessageCircle, Server, Settings as SettingsIcon, SlidersHorizontal, Video } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import CamerasSettings from '../components/settings/CamerasSettings'
import RecordersSettings from '../components/settings/RecordersSettings'
import DetectionSettings from '../components/settings/DetectionSettings'
import RecipientsEditor from '../components/settings/RecipientsEditor'
import StorageSettings from '../components/settings/StorageSettings'
import SystemStatus from '../components/settings/SystemStatus'
import './Metrics.css'

// WhatsApp alerts: is it on, who gets the messages, and a button to try it
function Notifications({ isAdmin }) {
  const [info, setInfo] = useState(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const loadStatus = useCallback(
    () =>
      fetch('/api/notifications/status')
        .then(response => (response.ok ? response.json() : null))
        .then(setInfo)
        .catch(() => setInfo(null)),
    []
  )

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  async function call(path, okText) {
    setBusy(true)
    setMessage('')
    try {
      const response = await fetch(path, { method: 'POST' })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Request failed')
      const failed = (data.results || []).filter(item => !item.ok)
      setMessage(failed.length ? `Failed: ${failed[0].error}` : data.status === 'failed'
        ? `Failed: ${(data.errors || []).join('; ')}` : data.status === 'no_recipients'
          ? 'Add a recipient first.' : okText)
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
              {info.use_templates
                ? 'Using the approved message template.'
                : 'Falls back to the approved template outside the 24 hour window.'}
            </p>
            <RecipientsEditor isAdmin={isAdmin} masked={info.recipients} onChanged={loadStatus} />
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
                disabled={busy || info.recipients.length === 0}
                onClick={() => call('/api/notifications/test', 'Test message sent.')}
              >
                Send a test to everyone
              </button>
              <button
                type="button"
                className="mt-submit"
                disabled={busy || info.recipients.length === 0}
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
  const [isAdmin, setIsAdmin] = useState(false)

  // Only administrators can change values; everyone can look
  useEffect(() => {
    fetch('/api/auth/me')
      .then(response => (response.ok ? response.json() : null))
      .then(user => setIsAdmin(user?.role === 'admin'))
      .catch(() => setIsAdmin(false))
  }, [])

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

        <button
          role="tab"
          aria-selected={activeTab === 'detection'}
          className={activeTab === 'detection' ? 'selected' : ''}
          onClick={() => setActiveTab('detection')}
        >
          <SlidersHorizontal size={18} />
          Detection
        </button>

        <button
          role="tab"
          aria-selected={activeTab === 'storage'}
          className={activeTab === 'storage' ? 'selected' : ''}
          onClick={() => setActiveTab('storage')}
        >
          <HardDrive size={18} />
          Storage
        </button>

        <button
          role="tab"
          aria-selected={activeTab === 'system'}
          className={activeTab === 'system' ? 'selected' : ''}
          onClick={() => setActiveTab('system')}
        >
          <Activity size={18} />
          System
        </button>
      </div>

      {activeTab === 'cameras' && <CamerasSettings />}

      {activeTab === 'notifications' && <Notifications isAdmin={isAdmin} />}

      {activeTab === 'detection' && <DetectionSettings isAdmin={isAdmin} />}

      {activeTab === 'storage' && <StorageSettings isAdmin={isAdmin} />}

      {activeTab === 'system' && <SystemStatus />}

      {activeTab === 'recorders' && <RecordersSettings isAdmin={isAdmin} />}
    </div>
  )
}

export default Settings
