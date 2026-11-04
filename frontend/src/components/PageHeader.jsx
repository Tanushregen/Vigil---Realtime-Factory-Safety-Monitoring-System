import { useEffect, useState } from 'react'
import { Bell, House, RefreshCw } from 'lucide-react'
import './PageHeader.css'

const ALERT_REFRESH_MS = 30000

// Breadcrumb, notification bell and page title shared by the redesigned pages
function PageHeader({
  icon: Icon,
  title,
  subtitle,
  crumbs,
  onOpenAlerts,
  onRefresh,
  refreshing = false,
}) {
  const [recentAlerts, setRecentAlerts] = useState(0)

  // The alert badge is optional: a failed request just leaves it hidden
  useEffect(() => {
    let cancelled = false

    function loadAlerts() {
      fetch('/api/violations?since_hours=24&limit=1')
        .then(response => (response.ok ? response.json() : null))
        .then(data => {
          if (!cancelled) setRecentAlerts(data?.total || 0)
        })
        .catch(() => {})
    }

    loadAlerts()
    const timer = setInterval(loadAlerts, ALERT_REFRESH_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const bellLabel = recentAlerts
    ? `${recentAlerts} alerts in the last 24 hours`
    : 'No alerts in the last 24 hours'

  return (
    <>
      <div className="ph-topline">
        <nav className="ph-breadcrumb" aria-label="Breadcrumb">
          <House size={18} />
          {crumbs.map((crumb, index) => (
            <span key={crumb} className="ph-crumb">
              {index > 0 && <i>/</i>}
              {index === crumbs.length - 1 ? (
                <strong>{crumb}</strong>
              ) : (
                <span>{crumb}</span>
              )}
            </span>
          ))}
        </nav>

        <button
          className="ph-bell"
          onClick={onOpenAlerts}
          aria-label={bellLabel}
          title={bellLabel}
        >
          <Bell size={21} />
          {recentAlerts > 0 && <span className="ph-bell-dot" />}
        </button>
      </div>

      <header className="ph-header">
        <div className="ph-icon">
          <Icon size={34} strokeWidth={1.7} />
        </div>

        <div className="ph-text">
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>

        {onRefresh && (
          <button
            className="ph-refresh"
            onClick={onRefresh}
            disabled={refreshing}
          >
            <RefreshCw size={19} className={refreshing ? 'ph-spin' : ''} />
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        )}
      </header>
    </>
  )
}

export default PageHeader
