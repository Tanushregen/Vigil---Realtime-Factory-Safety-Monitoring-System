import { useCallback, useEffect, useState } from 'react'
import { Send, Trash2 } from 'lucide-react'
import './SettingsPanels.css'

async function request(path, options) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(' ') : data.detail
    throw new Error(detail || 'Request failed')
  }
  return data
}

// Who receives the WhatsApp messages. Changes apply to the very next message, with no restart.
// The step every phone needs once: message the business number so WhatsApp lets alerts through
function ConnectBox({ businessNumber }) {
  const digits = (businessNumber || '').replace(/\D/g, '')
  return (
    <div className="sp-connect">
      <strong>Connect a phone to receive alerts</strong>
      <p>
        Send <b>&quot;hi&quot;</b> on WhatsApp to{' '}
        <b className="sp-number">{businessNumber || 'your WhatsApp business number'}</b>, then add that phone&apos;s
        number to the list below.
      </p>
      {digits && (
        <a className="sp-link" href={`https://wa.me/${digits}?text=hi`} target="_blank" rel="noreferrer">
          Open WhatsApp and say hi
        </a>
      )}
    </div>
  )
}

function RecipientsEditor({ isAdmin, masked, businessNumber, onChanged }) {
  const [data, setData] = useState(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    if (!isAdmin) return
    try {
      setData(await request('/api/notifications/recipients'))
    } catch (err) {
      setError(err.message)
    }
  }, [isAdmin])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  async function run(work, done) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await work()
      if (done) setMessage(done)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const save = list =>
    request('/api/notifications/recipients', { method: 'PUT', body: JSON.stringify({ recipients: list }) })
      .then(async result => {
        setData(result)
        await onChanged()
      })

  const add = event => {
    event.preventDefault()
    if (!input.trim()) return
    run(async () => {
      await save([...(data?.recipients || []), input])
      setInput('')
    }, 'Added. It applies to the next message.')
  }

  const remove = number =>
    run(() => save(data.recipients.filter(item => item !== number)), 'Removed.')

  const test = number =>
    run(async () => {
      const result = await request('/api/notifications/test', {
        method: 'POST',
        body: JSON.stringify({ to: number }),
      })
      const failed = (result.results || []).find(item => !item.ok)
      if (failed) throw new Error(failed.error)
    }, 'Test message sent. Check that phone.')

  const useEnv = () =>
    run(async () => {
      setData(await request('/api/notifications/recipients', { method: 'DELETE' }))
      await onChanged()
    }, 'Using the list from .env again.')

  const numbers = data?.recipients || []

  if (!isAdmin) {
    return (
      <div className="sp-recipients">
        <ConnectBox businessNumber={businessNumber} />
        <strong>Who receives the messages</strong>
        <p className="sp-note inline">
          {masked.length ? masked.join(', ') : 'Nobody yet.'} Only an administrator can change this list.
        </p>
      </div>
    )
  }

  return (
    <div className="sp-recipients">
      <ConnectBox businessNumber={businessNumber} />
      <div className="sp-recipients-head">
        <strong>Who receives the messages</strong>
        <small>
          {data?.source === 'settings' ? 'Saved here' : data ? 'From .env (nothing saved here yet)' : ''}
        </small>
      </div>

      {data && numbers.length === 0 && (
        <p className="sp-note inline">
          No numbers yet, so no message is sent. Add one below.
        </p>
      )}

      <ul>
        {numbers.map(number => (
          <li key={number}>
            <span className="sp-number">+{number}</span>
            <button type="button" className="sp-link" disabled={busy} onClick={() => test(number)}>
              <Send size={14} /> Send a test
            </button>
            <button type="button" className="sp-link danger" disabled={busy} onClick={() => remove(number)}>
              <Trash2 size={14} /> Remove
            </button>
          </li>
        ))}
      </ul>

      <form className="sp-add" onSubmit={add}>
        <input
          className="sp-input wide"
          value={input}
          disabled={busy || !data}
          placeholder="919876543210 (country code first)"
          inputMode="tel"
          onChange={event => setInput(event.target.value)}
          aria-label="Phone number to add"
        />
        <button type="submit" className="mt-submit" disabled={busy || !data || !input.trim()}>
          Add
        </button>
      </form>

      <p className="sp-note inline">
        Digits with the country code, for example 919876543210 for India. A + or spaces are fine, and you can paste
        several numbers at once.
      </p>

      {data?.source === 'settings' && data.env_count > 0 && (
        <button type="button" className="sp-link" disabled={busy} onClick={useEnv}>
          Use the {data.env_count} number(s) from .env instead
        </button>
      )}

      {error && <p className="error-message">{error}</p>}
      {message && <p className="sp-ok" role="status">{message}</p>}
    </div>
  )
}

export default RecipientsEditor
