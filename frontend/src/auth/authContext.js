import { createContext, useContext } from 'react'

export const AuthContext = createContext(null)

export function useAuth() {
  const context = useContext(AuthContext)

  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider')
  }

  return context
}

// Turn a FastAPI error response body into a readable message
export function errorMessage(data, fallback) {
  const detail = data?.detail

  if (typeof detail === 'string') {
    return detail
  }

  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map(item => String(item.msg || '').replace(/^Value error, /, ''))
      .join(' ')
  }

  return fallback
}
