import { useCallback, useEffect, useMemo, useState } from 'react'
import { AuthContext, errorMessage } from './authContext'

// Auth endpoints whose 401 responses are expected and handled by the caller
const AUTH_FLOW_PATHS = [
  '/api/auth/login',
  '/api/auth/logout',
  '/api/auth/me',
  '/api/auth/forgot-password',
  '/api/auth/reset-password',
]

function requestPath(input) {
  const url = typeof input === 'string' ? input : input?.url || ''

  try {
    return new URL(url, window.location.origin).pathname
  } catch {
    return ''
  }
}

function AuthProvider({ children }) {
  const [user, setUser] = useState(null)

  // loading, authenticated or anonymous
  const [status, setStatus] = useState('loading')
  const [sessionMessage, setSessionMessage] = useState('')

  // Restore an existing session from the HttpOnly cookie
  useEffect(() => {
    let cancelled = false

    async function loadCurrentUser() {
      try {
        const response = await fetch('/api/auth/me')

        if (cancelled) return

        if (response.ok) {
          setUser(await response.json())
          setStatus('authenticated')
        } else {
          setStatus('anonymous')
        }
      } catch {
        if (cancelled) return
        setSessionMessage(
          'Cannot connect to the backend. Check that FastAPI is running.'
        )
        setStatus('anonymous')
      }
    }

    loadCurrentUser()

    return () => {
      cancelled = true
    }
  }, [])

  // Send the user back to the login page when any API call reports
  // that the session has expired or been revoked
  useEffect(() => {
    const originalFetch = window.fetch

    window.fetch = async (...args) => {
      const response = await originalFetch(...args)
      const path = requestPath(args[0])

      if (
        response.status === 401 &&
        path.startsWith('/api/') &&
        !AUTH_FLOW_PATHS.includes(path)
      ) {
        setUser(null)
        setStatus('anonymous')
        setSessionMessage('Your session has expired. Please sign in again.')
      }

      return response
    }

    return () => {
      window.fetch = originalFetch
    }
  }, [])

  const login = useCallback(async (email, password, rememberMe) => {
    let response

    try {
      response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          password,
          remember_me: rememberMe,
        }),
      })
    } catch {
      throw new Error(
        'Cannot connect to the server. Please try again shortly.'
      )
    }

    const data = await response.json().catch(() => ({}))

    if (!response.ok) {
      throw new Error(errorMessage(data, 'Unable to sign in.'))
    }

    setSessionMessage('')
    setUser(data.user)
    setStatus('authenticated')
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' })
    } catch {
      // The local session is cleared even if the server is unreachable
    }

    setSessionMessage('')
    setUser(null)
    setStatus('anonymous')
  }, [])

  const value = useMemo(
    () => ({
      user,
      status,
      sessionMessage,
      isAdmin: user?.role === 'admin',
      login,
      logout,
    }),
    [user, status, sessionMessage, login, logout]
  )

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}

export default AuthProvider
