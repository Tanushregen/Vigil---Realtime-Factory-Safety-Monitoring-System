import { useEffect, useState } from 'react'
import { errorMessage, useAuth } from '../auth/authContext'
import lightLogo from '../assets/vigil-logo-light.png'
import darkLogo from '../assets/vigil-logo-dark.png'
import './Login.css'

const ADMIN_CONTACT_EMAIL = import.meta.env.VITE_ADMIN_CONTACT_EMAIL

const features = [
  {
    icon: 'shield',
    title: 'Intelligent Threat Detection',
    text: 'AI spots unsafe behaviour and hazards as they happen.',
  },
  {
    icon: 'bell',
    title: 'Real-time Alerts',
    text: 'Instant notifications when a safety rule is broken.',
  },
  {
    icon: 'chart',
    title: 'Advanced Analytics',
    text: 'Track incidents and trends across every zone.',
  },
]

function Icon({ name }) {
  const paths = {
    shield: (
      <>
        <path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.2 7.5 9.5 4.3-1.3 7.5-4.9 7.5-9.5V6L12 3Z" />
        <path d="m8.8 12 2.2 2.2 4.3-4.4" />
      </>
    ),
    bell: (
      <>
        <path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 1.5h-15L6 16.5Z" />
        <path d="M10 20.5a2.2 2.2 0 0 0 4 0" />
      </>
    ),
    chart: (
      <>
        <path d="M4 20h16" />
        <path d="M7 16v-4M12 16V7M17 16v-6" />
      </>
    ),
    mail: (
      <>
        <rect x="3.5" y="5.5" width="17" height="13" rx="2" />
        <path d="m4 7 8 6 8-6" />
      </>
    ),
    lock: (
      <>
        <rect x="5" y="10.5" width="14" height="10" rx="2" />
        <path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3" />
      </>
    ),
    eye: (
      <>
        <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
        <circle cx="12" cy="12" r="3" />
      </>
    ),
    eyeOff: (
      <>
        <path d="M10.6 5.6A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-2.9 3.6M6.4 6.9C3.9 8.6 2.5 12 2.5 12S6 18.5 12 18.5a9 9 0 0 0 4.6-1.3" />
        <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M3 3l18 18" />
      </>
    ),
  }

  return (
    <svg
      className="login-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  )
}

function VigilLogo({ light = false }) {
  return (
    <div className={`vigil-brand ${light ? 'light' : ''}`}>
      <img
        className="vigil-brand-image"
        src={light ? lightLogo : darkLogo}
        alt="VIGIL"
      />
    </div>
  )
}

function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  placeholder = 'Enter your password',
}) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="login-field">
      <label htmlFor={id}>{label}</label>
      <div className="login-input">
        <Icon name="lock" />
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={event => onChange(event.target.value)}
          placeholder={placeholder}
          autoComplete={autoComplete}
          required
        />
        <button
          type="button"
          className="login-toggle"
          onClick={() => setVisible(previous => !previous)}
          aria-label={visible ? 'Hide password' : 'Show password'}
          aria-pressed={visible}
        >
          <Icon name={visible ? 'eyeOff' : 'eye'} />
        </button>
      </div>
    </div>
  )
}

function Login({ resetToken = null, onResetDone }) {
  const { login, sessionMessage } = useAuth()

  // login, forgot or reset
  const [mode, setMode] = useState(resetToken ? 'reset' : 'login')

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [rememberMe, setRememberMe] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [resetEnabled, setResetEnabled] = useState(false)
  const [showContact, setShowContact] = useState(false)

  // Ask the backend whether password reset by email is available
  useEffect(() => {
    fetch('/api/auth/config')
      .then(response => (response.ok ? response.json() : null))
      .then(data => setResetEnabled(Boolean(data?.password_reset_enabled)))
      .catch(() => setResetEnabled(false))
  }, [])

  function switchMode(nextMode) {
    setMode(nextMode)
    setError('')
    setNotice('')
    setPassword('')
    setConfirmPassword('')
  }

  function handleForgotClick(event) {
    event.preventDefault()

    if (resetEnabled) {
      switchMode('forgot')
    } else {
      setError('')
      setNotice(
        'Password reset by email is not available. ' +
        'Contact your administrator to reset your password.'
      )
    }
  }

  async function handleLogin(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setSubmitting(true)

    try {
      await login(email.trim(), password, rememberMe)
    } catch (err) {
      setError(err.message)
      setPassword('')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleForgot(event) {
    event.preventDefault()
    setError('')
    setNotice('')
    setSubmitting(true)

    try {
      const response = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim() }),
      })
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(errorMessage(data, 'Unable to request a reset link.'))
      }

      setNotice(data.message)
    } catch (err) {
      setError(
        err instanceof TypeError
          ? 'Cannot connect to the server. Please try again shortly.'
          : err.message
      )
    } finally {
      setSubmitting(false)
    }
  }

  async function handleReset(event) {
    event.preventDefault()
    setError('')
    setNotice('')

    if (password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setSubmitting(true)

    try {
      const response = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, new_password: password }),
      })
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(errorMessage(data, 'Unable to reset password.'))
      }

      window.history.replaceState(null, '', '/login')
      switchMode('login')
      setNotice(data.message)
      onResetDone?.()
    } catch (err) {
      setError(
        err instanceof TypeError
          ? 'Cannot connect to the server. Please try again shortly.'
          : err.message
      )
    } finally {
      setSubmitting(false)
    }
  }

  const headings = {
    login: ['Welcome Back', 'Sign in to your account to continue.'],
    forgot: [
      'Forgot Password',
      'Enter your account email and we will send you a reset link.',
    ],
    reset: ['Set a New Password', 'Choose a new password for your account.'],
  }
  const [title, subtitle] = headings[mode]

  return (
    <div className="login-page">
      <main className="login-layout">
        <section className="login-hero">
          <VigilLogo light />

          <div className="login-hero-copy">
            <h1>
              Smarter Surveillance.
              <br />
              <span>Safer Tomorrow.</span>
            </h1>
            <p>AI-powered monitoring for a safer, more secure world.</p>
          </div>

          <ul className="login-features">
            {features.map(feature => (
              <li key={feature.title}>
                <span className="login-feature-icon">
                  <Icon name={feature.icon} />
                </span>
                <div>
                  <h3>{feature.title}</h3>
                  <p>{feature.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="login-panel">
          <div className="login-card">
            <VigilLogo />

            <h2>{title}</h2>
            <p className="login-subtitle">{subtitle}</p>

            {mode === 'login' && sessionMessage && !error && !notice && (
              <div className="login-alert info" role="status">
                {sessionMessage}
              </div>
            )}

            {error && (
              <div className="login-alert error" role="alert">
                {error}
              </div>
            )}

            {notice && (
              <div className="login-alert info" role="status">
                {notice}
              </div>
            )}

            {mode === 'login' && (
              <form onSubmit={handleLogin}>
              <div className="login-field">
                <label htmlFor="login-email">Email Address</label>
                <div className="login-input">
                  <Icon name="mail" />
                  <input
                    id="login-email"
                    type="email"
                    value={email}
                    onChange={event => setEmail(event.target.value)}
                    placeholder="Email Address"
                    autoComplete="username"
                    autoFocus
                    required
                  />
                </div>
              </div>

              <PasswordField
                id="login-password"
                label="Password"
                value={password}
                onChange={setPassword}
                autoComplete="current-password"
                placeholder="Password"
              />

              <div className="login-row">
                <label className="login-check">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={event => setRememberMe(event.target.checked)}
                  />
                  <span>Remember me</span>
                </label>

                <a href="#" className="login-link" onClick={handleForgotClick}>
                  Forgot password?
                </a>
              </div>

              <button
                type="submit"
                className="login-submit"
                disabled={submitting}
              >
                {submitting && <span className="auth-spinner small" />}
                {submitting ? 'Signing in…' : 'Sign In'}
              </button>
              </form>
            )}

            {mode === 'forgot' && (
              <form onSubmit={handleForgot}>
              <div className="login-field">
                <label htmlFor="forgot-email">Email Address</label>
                <div className="login-input">
                  <Icon name="mail" />
                  <input
                    id="forgot-email"
                    type="email"
                    value={email}
                    onChange={event => setEmail(event.target.value)}
                    placeholder="Enter your email"
                    autoComplete="username"
                    autoFocus
                    required
                  />
                </div>
              </div>

              <button
                type="submit"
                className="login-submit"
                disabled={submitting}
              >
                {submitting && <span className="auth-spinner small" />}
                {submitting ? 'Sending…' : 'Send Reset Link'}
              </button>

              <button
                type="button"
                className="login-back"
                onClick={() => switchMode('login')}
              >
                Back to sign in
              </button>
              </form>
            )}

            {mode === 'reset' && (
              <form onSubmit={handleReset}>
              <PasswordField
                id="reset-password"
                label="New Password"
                value={password}
                onChange={setPassword}
                autoComplete="new-password"
              />

              <PasswordField
                id="reset-confirm"
                label="Confirm New Password"
                value={confirmPassword}
                onChange={setConfirmPassword}
                autoComplete="new-password"
              />

              <p className="login-hint">
                At least 8 characters, including letters and numbers.
              </p>

              <button
                type="submit"
                className="login-submit"
                disabled={submitting}
              >
                {submitting && <span className="auth-spinner small" />}
                {submitting ? 'Saving…' : 'Reset Password'}
              </button>

              <button
                type="button"
                className="login-back"
                onClick={() => {
                  window.history.replaceState(null, '', '/login')
                  switchMode('login')
                  onResetDone?.()
                }}
              >
                Back to sign in
              </button>
              </form>
            )}

            <p className="login-contact">
              Don&apos;t have an account?{' '}
              {ADMIN_CONTACT_EMAIL ? (
                <a
                  className="login-link"
                  href={`mailto:${ADMIN_CONTACT_EMAIL}?subject=VIGIL%20access%20request`}
                >
                  Contact Administrator
                </a>
              ) : (
                <button
                  type="button"
                  className="login-link as-button"
                  onClick={() => setShowContact(previous => !previous)}
                  aria-expanded={showContact}
                >
                  Contact Administrator
                </button>
              )}
            </p>

            {showContact && (
              <p className="login-contact-note">
                VIGIL accounts are created by your administrator. Ask them to
                set up access for you or to reset your password.
              </p>
            )}
          </div>
        </section>
      </main>

      <footer className="login-footer">
        <span><strong>VIGIL</strong><span aria-hidden="true"> · </span>AI Security Platform</span>
        <span>Secure <span aria-hidden="true">·</span> Smart <span aria-hidden="true">·</span> Reliable</span>
      </footer>
    </div>
  )
}

export default Login
