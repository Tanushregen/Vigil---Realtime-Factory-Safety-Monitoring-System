import { useEffect, useState } from 'react'
import { errorMessage, useAuth } from '../auth/authContext'
import './Users.css'

const emptyForm = {
  name: '',
  email: '',
  password: '',
  role: 'user',
  is_active: true,
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json().catch(() => ({}))

  if (!response.ok) {
    throw new Error(errorMessage(data, 'Request failed'))
  }

  return data
}

function formatDate(value) {
  if (!value) return 'Never'
  // The API returns naive UTC timestamps
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  return date.toLocaleString()
}

function Users() {
  const { user: currentUser } = useAuth()

  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const [form, setForm] = useState(emptyForm)
  const [editingId, setEditingId] = useState(null)
  const [saving, setSaving] = useState(false)

  async function loadUsers() {
    try {
      setUsers(await request('/api/auth/users'))
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    request('/api/auth/users')
      .then(data => {
        if (!cancelled) setUsers(data)
      })
      .catch(err => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  function updateField(field, value) {
    setForm(previous => ({ ...previous, [field]: value }))
  }

  function startEdit(user) {
    setEditingId(user.id)
    setForm({
      name: user.name,
      email: user.email,
      password: '',
      role: user.role,
      is_active: user.is_active,
    })
    setError('')
    setSuccess('')
  }

  function cancelEdit() {
    setEditingId(null)
    setForm(emptyForm)
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    setSuccess('')

    try {
      if (editingId) {
        const changes = {
          name: form.name,
          email: form.email,
          role: form.role,
          is_active: form.is_active,
        }

        // Blank password keeps the current one
        if (form.password) {
          changes.password = form.password
        }

        await request(`/api/auth/users/${editingId}`, {
          method: 'PATCH',
          body: JSON.stringify(changes),
        })
        setSuccess('User updated successfully.')
      } else {
        await request('/api/auth/users', {
          method: 'POST',
          body: JSON.stringify(form),
        })
        setSuccess('User created successfully.')
      }

      cancelEdit()
      await loadUsers()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function toggleActive(user) {
    setError('')
    setSuccess('')

    try {
      await request(`/api/auth/users/${user.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: !user.is_active }),
      })
      setSuccess(
        `${user.name} has been ${user.is_active ? 'disabled' : 'enabled'}.`
      )
      await loadUsers()
    } catch (err) {
      setError(err.message)
    }
  }

  const editingSelf = editingId === currentUser.id

  return (
    <div className="users-page">
      <header className="topbar">
        <div>
          <h1>Users</h1>
          <p className="subtitle">
            Create accounts and manage who can access VIGIL
          </p>
        </div>
      </header>

      {error && <p className="error-message">{error}</p>}
      {success && <p className="users-success">{success}</p>}

      <section className="users-card">
        <h2>{editingId ? 'Edit User' : 'Add User'}</h2>

        <form className="users-form" onSubmit={handleSubmit}>
          <label>
            Name
            <input
              value={form.name}
              onChange={event => updateField('name', event.target.value)}
              required
              maxLength={100}
            />
          </label>

          <label>
            Email
            <input
              type="email"
              value={form.email}
              onChange={event => updateField('email', event.target.value)}
              required
            />
          </label>

          <label>
            {editingId ? 'New password (optional)' : 'Password'}
            <input
              type="password"
              value={form.password}
              onChange={event => updateField('password', event.target.value)}
              required={!editingId}
              minLength={8}
              maxLength={128}
              autoComplete="new-password"
              placeholder={editingId ? 'Leave blank to keep' : 'Min. 8 characters, letters and numbers'}
            />
          </label>

          <label>
            Role
            <select
              value={form.role}
              onChange={event => updateField('role', event.target.value)}
              disabled={editingSelf}
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </label>

          <label className="users-checkbox">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={event => updateField('is_active', event.target.checked)}
              disabled={editingSelf}
            />
            Active
          </label>

          <div className="users-actions">
            <button
              type="submit"
              className="refresh-button"
              disabled={saving}
            >
              {saving ? 'Saving…' : editingId ? 'Save Changes' : 'Create User'}
            </button>

            {editingId && (
              <button
                type="button"
                className="users-secondary"
                onClick={cancelEdit}
              >
                Cancel
              </button>
            )}
          </div>
        </form>
      </section>

      <section className="users-card">
        <div className="section-heading">
          <h2>All Users</h2>
          <span>{users.length} users</span>
        </div>

        {loading ? (
          <p className="empty-message">Loading users...</p>
        ) : (
          <div className="users-table-wrap">
            <table className="users-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Last login</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.map(user => (
                  <tr key={user.id}>
                    <td>
                      {user.name}
                      {user.id === currentUser.id && (
                        <span className="users-you"> (you)</span>
                      )}
                    </td>
                    <td>{user.email}</td>
                    <td>
                      <span className={`users-badge ${user.role}`}>
                        {user.role}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`users-badge ${
                          user.is_active ? 'active' : 'disabled'
                        }`}
                      >
                        {user.is_active ? 'Active' : 'Disabled'}
                      </span>
                    </td>
                    <td>{formatDate(user.last_login)}</td>
                    <td className="users-row-actions">
                      <button
                        type="button"
                        className="users-secondary"
                        onClick={() => startEdit(user)}
                      >
                        Edit
                      </button>
                      {user.id !== currentUser.id && (
                        <button
                          type="button"
                          className="users-secondary"
                          onClick={() => toggleActive(user)}
                        >
                          {user.is_active ? 'Disable' : 'Enable'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

export default Users
