
import { useEffect, useState } from "react";
import "./SafetyRules.css";

// Same-origin through the Vite proxy so the session cookie is sent
const API = "/api";

const steps = [
  { title: "Camera Groups", icon: "layers", subtitle: "Organize your cameras" },
  { title: "Universal Prompt", icon: "globe", subtitle: "Rules for all cameras" },
  { title: "Group Prompt", icon: "users", subtitle: "Rules for selected groups" },
  { title: "Individual Prompt", icon: "camera", subtitle: "Rules for specific cameras" },
  { title: "Review", icon: "eye", subtitle: "Review and save configuration" },
];

export default function SafetyRules() {
  const [currentStep, setCurrentStep] = useState(0);

  const [cameras, setCameras] = useState([]);
  const [groups, setGroups] = useState([]);
  const [savedPrompts, setSavedPrompts] = useState([]);

  const [groupName, setGroupName] = useState("");
  const [selectedCameras, setSelectedCameras] = useState([]);
  const [editingGroupId, setEditingGroupId] = useState(null);

  const [universalPrompts, setUniversalPrompts] = useState([]);
  const [groupPrompts, setGroupPrompts] = useState([]);
  const [individualPrompts, setIndividualPrompts] = useState([]);

  const [editingPromptId, setEditingPromptId] = useState(null);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    loadData();
  }, []);

  async function request(endpoint, options = {}) {
    const response = await fetch(`${API}${endpoint}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Request failed");
    }

    if (response.status === 204) return null;
    return response.json();
  }

  async function loadData() {
    setLoading(true);
    setError("");

    try {
      const [cameraData, groupData, promptData] = await Promise.all([
        request("/cameras"),
        request("/groups"),
        request("/prompts"),
      ]);

      setCameras(Array.isArray(cameraData) ? cameraData : []);
      setGroups(Array.isArray(groupData) ? groupData : []);
      setSavedPrompts(Array.isArray(promptData) ? promptData : []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function cameraName(id) {
    const camera = cameras.find(
      (item) => String(item.id) === String(id)
    );
    return camera?.name || `Camera ${id}`;
  }

  function groupNameById(id) {
    const group = groups.find(
      (item) => String(item.id) === String(id)
    );
    return group?.name || id;
  }

  function toggleCamera(id) {
    setSelectedCameras((previous) =>
      previous.includes(id)
        ? previous.filter((item) => item !== id)
        : [...previous, id]
    );
  }

  // ---------------- CAMERA GROUPS ----------------

  function resetGroupForm() {
    setGroupName("");
    setSelectedCameras([]);
    setEditingGroupId(null);
  }

  function editGroup(group) {
    setGroupName(group.name);
    setSelectedCameras(group.cameraIds || []);
    setEditingGroupId(group.id);
  }

  async function saveGroup() {
    if (!groupName.trim()) {
      setError("Enter a group name.");
      return;
    }

    if (!selectedCameras.length) {
      setError("Select at least one camera.");
      return;
    }

    setSaving(true);
    setError("");

    try {
      const payload = {
        name: groupName.trim(),
        camera_ids: selectedCameras,
      };

      await request(
        editingGroupId
          ? `/groups/${editingGroupId}`
          : "/groups",
        {
          method: editingGroupId ? "PATCH" : "POST",
          body: JSON.stringify(payload),
        }
      );

      resetGroupForm();
      await loadData();
      setSuccess("Camera group saved.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function deleteGroup(id) {
    if (!window.confirm("Delete this group?")) return;

    try {
      await request(`/groups/${id}`, { method: "DELETE" });
      await loadData();
      setSuccess("Group deleted.");
    } catch (err) {
      setError(err.message);
    }
  }

  // ---------------- PROMPT STATE ----------------

  function createEmptyPrompt(type) {
    return {
      localId: `${Date.now()}-${Math.random()}`,
      id: null,
      type,
      title: "",
      text: "",
      target: "",
    };
  }

  function addPrompt(type) {
    const setter = getPromptSetter(type);
    setter((previous) => [
      ...previous,
      createEmptyPrompt(type),
    ]);
  }

  function getPromptSetter(type) {
    if (type === "universal") return setUniversalPrompts;
    if (type === "group") return setGroupPrompts;
    return setIndividualPrompts;
  }

  function getPromptList(type) {
    if (type === "universal") return universalPrompts;
    if (type === "group") return groupPrompts;
    return individualPrompts;
  }

  function updatePrompt(type, localId, field, value) {
    const setter = getPromptSetter(type);

    setter((previous) =>
      previous.map((prompt) =>
        prompt.localId === localId
          ? { ...prompt, [field]: value }
          : prompt
      )
    );
  }

  function removePrompt(type, localId) {
    const setter = getPromptSetter(type);

    setter((previous) =>
      previous.filter((prompt) => prompt.localId !== localId)
    );
  }

  // ---------------- VALIDATION ----------------

  function validateStep() {
    setError("");

    if (currentStep === 0) {
      if (!cameras.length) {
        setError("Register cameras before configuring safety rules.");
        return false;
      }
    }

    const checks = [
      { step: 1, type: "universal" },
      { step: 2, type: "group" },
      { step: 3, type: "individual" },
    ];

    const check = checks.find((item) => item.step === currentStep);

    if (check) {
      const prompts = getPromptList(check.type);

      for (const prompt of prompts) {
        if (!prompt.title.trim() || !prompt.text.trim()) {
          setError("Complete the title and description of each prompt.");
          return false;
        }

        if (check.type !== "universal" && !prompt.target) {
          setError("Select a target for every prompt.");
          return false;
        }
      }

      if (check.type === "group" && prompts.length && !groups.length) {
        setError("Create a camera group first.");
        return false;
      }
    }

    if (currentStep === 4) {
      if (!allPrompts.length) {
        setError("Add at least one prompt before saving.");
        return false;
      }
    }

    return true;
  }

  function nextStep() {
    if (!validateStep()) return;
    setCurrentStep((previous) => Math.min(previous + 1, 4));
    setSuccess("");
  }

  function previousStep() {
    setCurrentStep((previous) => Math.max(previous - 1, 0));
    setError("");
  }

  const allPrompts = [
    ...universalPrompts,
    ...groupPrompts,
    ...individualPrompts,
  ];

  // ---------------- SAVE PROMPTS ----------------

  async function publishConfiguration() {
    if (!validateStep()) return;

    setSaving(true);
    setError("");
    setSuccess("");

    let savedCount = 0;

    try {
      for (const prompt of allPrompts) {
        const payload = {
          title: prompt.title.trim(),
          text: prompt.text.trim(),
          type: prompt.type,
          target:
            prompt.type === "universal" ? null : prompt.target,
        };

        await request(
          prompt.id ? `/prompts/${prompt.id}` : "/prompts",
          {
            method: prompt.id ? "PATCH" : "POST",
            body: JSON.stringify(payload),
          }
        );

        savedCount++;
      }

      setUniversalPrompts([]);
      setGroupPrompts([]);
      setIndividualPrompts([]);
      setEditingPromptId(null);
      setCurrentStep(0);

      await loadData();
      setSuccess(`${savedCount} prompt(s) saved successfully.`);
    } catch (err) {
      setError(
        `${savedCount} prompt(s) saved before the error: ${err.message}`
      );
      await loadData();
    } finally {
      setSaving(false);
    }
  }

  // ---------------- SAVED PROMPTS ----------------

  function editPrompt(prompt) {
    const item = {
      localId: `edit-${prompt.id}`,
      id: prompt.id,
      type: prompt.type,
      title: prompt.title,
      text: prompt.text,
      target: prompt.target || "",
    };

    setUniversalPrompts([]);
    setGroupPrompts([]);
    setIndividualPrompts([]);

    if (prompt.type === "universal") {
      setUniversalPrompts([item]);
      setCurrentStep(1);
    } else if (prompt.type === "group") {
      setGroupPrompts([item]);
      setCurrentStep(2);
    } else {
      setIndividualPrompts([item]);
      setCurrentStep(3);
    }

    setEditingPromptId(prompt.id);
    setError("");
  }

  async function togglePrompt(prompt) {
    try {
      await request(`/prompts/${prompt.id}/enabled`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !prompt.enabled }),
      });

      await loadData();
      setSuccess("Prompt status updated.");
    } catch (err) {
      setError(err.message);
    }
  }

  async function deletePrompt(id) {
    if (!window.confirm("Delete this prompt?")) return;

    try {
      await request(`/prompts/${id}`, { method: "DELETE" });
      await loadData();
      setSuccess("Prompt deleted.");
    } catch (err) {
      setError(err.message);
    }
  }

  // ---------------- PROMPT FORM ----------------

  function renderPromptForm(type) {
    const prompts = getPromptList(type);

    return (
      <div className="sr-stack">
        {prompts.length === 0 && (
          <p className="sr-empty">
            No prompts added yet. This step is optional.
          </p>
        )}

        {prompts.map((prompt, index) => (
          <div key={prompt.localId} className="sr-subcard">
            <div className="sr-subcard-head">
              <h3>Prompt {index + 1}</h3>

              <button
                type="button"
                onClick={() => removePrompt(type, prompt.localId)}
                className="sr-link sr-link-danger"
              >
                Remove
              </button>
            </div>

            <div className="sr-grid">
              <div className="sr-field">
                <label>
                  Prompt Title <span className="sr-required">*</span>
                </label>

                <input
                  value={prompt.title}
                  onChange={(e) =>
                    updatePrompt(type, prompt.localId, "title", e.target.value)
                  }
                  placeholder="e.g. Helmet required in work zone"
                />
              </div>

              {type === "universal" && (
                <div className="sr-field">
                  <label>Applies To</label>
                  <input value="All cameras" disabled />
                  <p className="sr-hint">
                    Universal prompts apply to every registered camera
                  </p>
                </div>
              )}

              {type === "group" && (
                <div className="sr-field">
                  <label>
                    Camera Group <span className="sr-required">*</span>
                  </label>

                  <select
                    value={prompt.target}
                    onChange={(e) =>
                      updatePrompt(type, prompt.localId, "target", e.target.value)
                    }
                  >
                    <option value="">- Select -</option>
                    {groups.map((group) => (
                      <option key={group.id} value={group.id}>
                        {group.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {type === "individual" && (
                <div className="sr-field">
                  <label>
                    Camera <span className="sr-required">*</span>
                  </label>

                  <select
                    value={prompt.target}
                    onChange={(e) =>
                      updatePrompt(type, prompt.localId, "target", e.target.value)
                    }
                  >
                    <option value="">- Select -</option>
                    {cameras.map((camera) => (
                      <option key={camera.id} value={camera.id}>
                        {cameraName(camera.id)}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="sr-field sr-span-2">
                <label>
                  Prompt Description <span className="sr-required">*</span>
                </label>

                <textarea
                  rows={4}
                  value={prompt.text}
                  onChange={(e) =>
                    updatePrompt(type, prompt.localId, "text", e.target.value)
                  }
                  placeholder="Describe what the AI should monitor..."
                />
              </div>
            </div>
          </div>
        ))}

        <button
          type="button"
          onClick={() => addPrompt(type)}
          className="sr-add-button"
        >
          + Add {prompts.length ? "Another " : ""}Prompt
        </button>
      </div>
    );
  }

  // ---------------- REVIEW ----------------

  function renderReview() {
    const sections = [
      { type: "universal", title: "Universal Prompts" },
      { type: "group", title: "Group Prompts" },
      { type: "individual", title: "Individual Prompts" },
    ];

    return (
      <div className="sr-stack">
        <div>
          <h3 className="sr-section-title">Camera Groups</h3>

          {groups.length ? (
            groups.map((group) => (
              <div key={group.id} className="sr-row">
                <div>
                  <p className="sr-row-title">{group.name}</p>
                  <p className="sr-row-meta">
                    {(group.cameraIds || []).map(cameraName).join(", ")}
                  </p>
                </div>
              </div>
            ))
          ) : (
            <p className="sr-empty">No camera groups created.</p>
          )}
        </div>

        {sections.map((section) => {
          const prompts = getPromptList(section.type);

          return (
            <div key={section.type}>
              <h3 className="sr-section-title">
                {section.title} ({prompts.length})
              </h3>

              {prompts.length ? (
                prompts.map((prompt, index) => (
                  <div key={prompt.localId} className="sr-row">
                    <div>
                      <p className="sr-row-title">
                        {index + 1}. {prompt.title}
                      </p>

                      <p className="sr-row-meta">
                        Target:{" "}
                        {section.type === "universal"
                          ? "All cameras"
                          : section.type === "group"
                            ? groupNameById(prompt.target)
                            : cameraName(prompt.target)}
                      </p>

                      <p className="sr-row-text">{prompt.text}</p>
                    </div>
                  </div>
                ))
              ) : (
                <p className="sr-empty">No prompts added.</p>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (loading) {
    return <div className="sr-page sr-loading">Loading Safety Rules...</div>;
  }

  return (
    <div className="sr-page">
      {/* PAGE HEADER */}

      <div className="sr-header">
        <button
          type="button"
          onClick={previousStep}
          disabled={currentStep === 0 || saving}
          className="sr-back"
          aria-label="Previous step"
        >
          <Icon name="arrow-left" />
        </button>

        <div>
          <h1>{editingPromptId ? "Edit Safety Rule" : "Safety Rules"}</h1>
          <p>Set up camera groups and AI safety monitoring prompts</p>
        </div>
      </div>

      {/* STEP INDICATOR */}

      <ol className="sr-stepper">
        {steps.map((item, index) => {
          const state =
            currentStep > index
              ? "completed"
              : currentStep === index
                ? "active"
                : "upcoming";

          return (
            <li key={item.title} className={`sr-step sr-step-${state}`}>
              <button
                type="button"
                onClick={() => setCurrentStep(index)}
                disabled={state !== "completed" || saving}
                title={item.subtitle}
              >
                <span className="sr-step-icon">
                  <Icon name={state === "completed" ? "check" : item.icon} />
                </span>
                <span className="sr-step-label">{item.title}</span>
              </button>

              {index !== steps.length - 1 && <span className="sr-step-line" />}
            </li>
          );
        })}
      </ol>

      {/* ERROR / SUCCESS */}

      {error && <div className="sr-alert sr-alert-error">{error}</div>}
      {success && <div className="sr-alert sr-alert-success">{success}</div>}

      {/* FORM CARD */}

      <div className="sr-card">
        {/* STAGE 1 */}

        {currentStep === 0 && (
          <div className="sr-stack">
            <div>
              <h2 className="sr-section-title">
                {editingGroupId ? "Edit Camera Group" : "Group Details"}
              </h2>

              <div className="sr-grid">
                <div className="sr-field">
                  <label>
                    Group Name <span className="sr-required">*</span>
                  </label>

                  <input
                    value={groupName}
                    onChange={(e) => setGroupName(e.target.value)}
                    placeholder="e.g. Warehouse Floor"
                  />
                </div>

                <div className="sr-field">
                  <label>Cameras</label>
                  <input
                    value={`${selectedCameras.length} of ${cameras.length} selected`}
                    disabled
                  />
                  <p className="sr-hint">Pick cameras from the list below</p>
                </div>
              </div>
            </div>

            <div>
              <h2 className="sr-section-title">Select Cameras</h2>

              {cameras.length ? (
                <div className="sr-camera-grid">
                  {cameras.map((camera) => (
                    <label
                      key={camera.id}
                      className={`sr-camera-tile ${
                        selectedCameras.includes(camera.id) ? "selected" : ""
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedCameras.includes(camera.id)}
                        onChange={() => toggleCamera(camera.id)}
                      />

                      <div>
                        <p className="sr-row-title">{cameraName(camera.id)}</p>
                        <p className="sr-row-meta">Camera ID: {camera.id}</p>
                      </div>
                    </label>
                  ))}
                </div>
              ) : (
                <p className="sr-empty">No cameras registered.</p>
              )}

              <div className="sr-inline-actions">
                <button
                  type="button"
                  onClick={saveGroup}
                  disabled={saving}
                  className="sr-button sr-button-primary"
                >
                  {saving
                    ? "Saving..."
                    : editingGroupId
                      ? "Update Group"
                      : "Create Group"}
                </button>

                {editingGroupId && (
                  <button
                    type="button"
                    onClick={resetGroupForm}
                    className="sr-button sr-button-secondary"
                  >
                    Cancel
                  </button>
                )}
              </div>
            </div>

            <div>
              <h2 className="sr-section-title">Existing Groups</h2>

              {groups.length === 0 && (
                <p className="sr-empty">No camera groups yet.</p>
              )}

              {groups.map((group) => (
                <div key={group.id} className="sr-row">
                  <div>
                    <p className="sr-row-title">{group.name}</p>
                    <p className="sr-row-meta">
                      {(group.cameraIds || []).map(cameraName).join(", ")}
                    </p>
                  </div>

                  <div className="sr-row-actions">
                    <button
                      type="button"
                      onClick={() => editGroup(group)}
                      className="sr-link"
                    >
                      Edit
                    </button>

                    <button
                      type="button"
                      onClick={() => deleteGroup(group.id)}
                      className="sr-link sr-link-danger"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* STAGE 2 */}

        {currentStep === 1 && (
          <div>
            <h2 className="sr-section-title">Universal Prompts</h2>
            <p className="sr-section-sub">
              Universal prompts apply to every registered camera.
            </p>
            {renderPromptForm("universal")}
          </div>
        )}

        {/* STAGE 3 */}

        {currentStep === 2 && (
          <div>
            <h2 className="sr-section-title">Group Prompts</h2>
            <p className="sr-section-sub">
              Assign each prompt to a camera group. Multiple prompts can be
              created for different groups.
            </p>
            {renderPromptForm("group")}
          </div>
        )}

        {/* STAGE 4 */}

        {currentStep === 3 && (
          <div>
            <h2 className="sr-section-title">Individual Prompts</h2>
            <p className="sr-section-sub">
              Assign each prompt to an individual camera.
            </p>
            {renderPromptForm("individual")}
          </div>
        )}

        {/* STAGE 5 */}

        {currentStep === 4 && renderReview()}

        {/* NAVIGATION */}

        <div className="sr-footer">
          <button
            type="button"
            onClick={previousStep}
            disabled={currentStep === 0 || saving}
            className="sr-button sr-button-secondary"
          >
            Back
          </button>

          {currentStep < 4 ? (
            <button
              type="button"
              onClick={nextStep}
              disabled={saving}
              className="sr-button sr-button-primary"
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              onClick={publishConfiguration}
              disabled={saving || allPrompts.length === 0}
              className="sr-button sr-button-primary"
            >
              {saving ? "Publishing..." : "Publish Configuration"}
            </button>
          )}
        </div>
      </div>

      {/* SAVED PROMPTS */}

      <div className="sr-card">
        <h2 className="sr-section-title">Saved Safety Prompts</h2>
        <p className="sr-section-sub">
          Manage your existing safety monitoring prompts.
        </p>

        {savedPrompts.length === 0 ? (
          <p className="sr-empty">No saved prompts yet.</p>
        ) : (
          savedPrompts.map((prompt) => (
            <div key={prompt.id} className="sr-row">
              <div>
                <p className="sr-row-title">
                  {prompt.title}
                  <span
                    className={`sr-badge ${
                      prompt.enabled ? "sr-badge-on" : "sr-badge-off"
                    }`}
                  >
                    {prompt.enabled ? "Enabled" : "Disabled"}
                  </span>
                </p>

                <p className="sr-row-meta">
                  {prompt.type} ·{" "}
                  {prompt.type === "universal"
                    ? "All cameras"
                    : prompt.type === "group"
                      ? groupNameById(prompt.target)
                      : cameraName(prompt.target)}
                </p>

                <p className="sr-row-text">{prompt.text}</p>
              </div>

              <div className="sr-row-actions">
                <button
                  type="button"
                  onClick={() => editPrompt(prompt)}
                  className="sr-link"
                >
                  Edit
                </button>

                <button
                  type="button"
                  onClick={() => togglePrompt(prompt)}
                  className="sr-link sr-link-muted"
                >
                  {prompt.enabled ? "Disable" : "Enable"}
                </button>

                <button
                  type="button"
                  onClick={() => deletePrompt(prompt.id)}
                  className="sr-link sr-link-danger"
                >
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// Outline icons (Lucide-style) so the page needs no icon dependency
const ICON_PATHS = {
  "arrow-left": "M19 12H5 M12 19l-7-7 7-7",
  check: "M20 6 9 17l-5-5",
  layers: "M12 2 2 7l10 5 10-5-10-5z M2 17l10 5 10-5 M2 12l10 5 10-5",
  globe:
    "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z M2 12h20 M12 2a15 15 0 0 1 0 20 M12 2a15 15 0 0 0 0 20",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75",
  camera:
    "M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z M12 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
};

function Icon({ name }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}
