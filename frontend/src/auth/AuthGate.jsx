import { useEffect, useState } from 'react'
import App from '../App'
import Login from '../pages/Login'
import { useAuth } from './authContext'

const RESET_PATH = '/reset-password'

function readResetToken() {
  if (window.location.pathname !== RESET_PATH) return null
  return new URLSearchParams(window.location.search).get('token')
}

// Protected-route gate: only signed-in users reach the VIGIL dashboard
function AuthGate() {
  const { status } = useAuth()
  const [resetToken, setResetToken] = useState(readResetToken)

  // Keep the address bar in step with what is shown
  useEffect(() => {
    if (resetToken) return

    const path = window.location.pathname

    if (status === 'authenticated' && path !== '/') {
      window.history.replaceState(null, '', '/')
    } else if (status === 'anonymous' && path !== '/login') {
      window.history.replaceState(null, '', '/login')
    }
  }, [status, resetToken])

  if (resetToken) {
    return (
      <Login
        resetToken={resetToken}
        onResetDone={() => setResetToken(null)}
      />
    )
  }

  if (status === 'loading') {
    return (
      <div className="auth-loading" role="status">
        <span className="auth-spinner" />
        <p>Loading VIGIL…</p>
      </div>
    )
  }

  return status === 'authenticated' ? <App /> : <Login />
}

export default AuthGate
