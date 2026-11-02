
import { useState } from 'react'

function Settings() {
  const [activeTab, setActiveTab] = useState('cameras')

  return (
    <div className="placeholder-page">
      <h1>Settings</h1>
      <p className="subtitle">Manage VIGIL configuration.</p>

      <div className="settings-tabs">
        <button
          className={activeTab === 'cameras' ? 'selected' : ''}
          onClick={() => setActiveTab('cameras')}
        >
          Cameras
        </button>

        <button
          className={activeTab === 'recorders' ? 'selected' : ''}
          onClick={() => setActiveTab('recorders')}
        >
          Recorders
        </button>
      </div>

      {activeTab === 'cameras' && (
        <div className="settings-content">
          <h2>Camera Settings</h2>
          <p>Camera configuration settings.</p>
        </div>
      )}

      {activeTab === 'recorders' && (
        <div className="settings-content">
          <h2>Recorder Settings</h2>
          <p>DVR/NVR configuration settings.</p>
        </div>
      )}
    </div>
  )
}

export default Settings
