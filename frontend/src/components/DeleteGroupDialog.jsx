import { useEffect, useState } from 'react'
import { Trash2, TriangleAlert, Unlink } from 'lucide-react'
import './DeleteGroupDialog.css'

// Caution pop-up for deleting a camera group. The user chooses between
// deleting only the group or the group together with its cameras.
function DeleteGroupDialog({
  groupName,
  cameraNames = [],
  busy = false,
  error = '',
  onCancel,
  onConfirm,
}) {
  const [choice, setChoice] = useState('group')
  const [understood, setUnderstood] = useState(false)

  const count = cameraNames.length
  const withCameras = choice === 'cameras'
  const cameraWord = `camera${count === 1 ? '' : 's'}`
  const canConfirm = !busy && (!withCameras || understood)

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === 'Escape' && !busy) onCancel()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [busy, onCancel])

  return (
    <div className="dg-overlay" onClick={busy ? undefined : onCancel}>
      <div
        className="dg-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dg-title"
        onClick={event => event.stopPropagation()}
      >
        <div className="dg-head">
          <span className="dg-warning">
            <TriangleAlert size={26} />
          </span>

          <div>
            <h2 id="dg-title">Delete group “{groupName}”?</h2>
            <p>
              {count > 0
                ? `This group has ${count} ${cameraWord}. Choose what happens to them.`
                : 'This group has no cameras.'}
            </p>
          </div>
        </div>

        {count > 0 && (
          <p className="dg-cameras" title={cameraNames.join(', ')}>
            {cameraNames.join(', ')}
          </p>
        )}

        <div className="dg-options" role="radiogroup" aria-label="Delete options">
          <label className={`dg-option ${choice === 'group' ? 'selected' : ''}`}>
            <input
              type="radio"
              name="dg-choice"
              checked={choice === 'group'}
              onChange={() => setChoice('group')}
              disabled={busy}
            />
            <Unlink size={22} />
            <span>
              <strong>Delete group only</strong>
              <small>
                The group and its safety prompts are removed. The {cameraWord}{' '}
                stay in VIGIL and become ungrouped.
              </small>
            </span>
          </label>

          {count > 0 && (
            <label
              className={`dg-option danger ${
                choice === 'cameras' ? 'selected' : ''
              }`}
            >
              <input
                type="radio"
                name="dg-choice"
                checked={choice === 'cameras'}
                onChange={() => setChoice('cameras')}
                disabled={busy}
              />
              <Trash2 size={22} />
              <span>
                <strong>Delete group and its cameras</strong>
                <small>
                  Permanently deletes the group and {count} {cameraWord},
                  including their recordings, violation clips and alerts. This
                  cannot be undone.
                </small>
              </span>
            </label>
          )}
        </div>

        {withCameras && (
          <label className="dg-understand">
            <input
              type="checkbox"
              checked={understood}
              onChange={event => setUnderstood(event.target.checked)}
              disabled={busy}
            />
            I understand the cameras and their footage will be permanently
            deleted.
          </label>
        )}

        {error && <p className="dg-error">{error}</p>}

        <div className="dg-actions">
          <button
            type="button"
            className="dg-cancel"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>

          <button
            type="button"
            className={`dg-confirm ${withCameras ? 'danger' : ''}`}
            onClick={() => onConfirm(withCameras)}
            disabled={!canConfirm}
          >
            {busy
              ? 'Deleting...'
              : withCameras
                ? 'Delete group and cameras'
                : 'Delete group'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default DeleteGroupDialog
