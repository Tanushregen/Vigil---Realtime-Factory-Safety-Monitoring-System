import { useState } from 'react'
import { Server, Settings as SettingsIcon, Video } from 'lucide-react'
import PageHeader from '../components/PageHeader'

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
