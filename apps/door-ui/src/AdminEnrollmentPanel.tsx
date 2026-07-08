import React, { useEffect, useState, useRef } from "react";
import { enrollmentApi, EnrolledPerson } from "./enrollmentApi";
import { LiveVideoPreview } from "@doorboard/ui-kit";

const ADMIN_TOKEN_KEY = "doorboard_admin_social_token";

export function AdminEnrollmentPanel() {
  const [token, setToken] = useState<string>(
    () => window.localStorage.getItem(ADMIN_TOKEN_KEY) || ""
  );
  const [tokenInput, setTokenInput] = useState(token);
  const [people, setPeople] = useState<EnrolledPerson[]>([]);
  const [privacyEnabled, setPrivacyEnabled] = useState(false);
  const [consentStatement, setConsentStatement] = useState({ text: "", version: "" });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Unenroll verification
  const [confirmUnenrollId, setConfirmUnenrollId] = useState<string | null>(null);

  // Wizard state
  const [showWizard, setShowWizard] = useState(false);
  const [wizardStep, setWizardStep] = useState<"consent" | "capture" | "profile" | "match">("consent");
  const [consentAgreed, setConsentAgreed] = useState(false);
  const [capturedBlobs, setCapturedBlobs] = useState<Blob[]>([]);
  const [capturedPreviews, setCapturedPreviews] = useState<string[]>([]);
  const [displayName, setDisplayName] = useState("");
  const [profileId, setProfileId] = useState("blue_wave");
  const [color, setColor] = useState("#0000ff");
  const [sound, setSound] = useState("");
  const [enrollResult, setEnrollResult] = useState<{ person_id: string; embeddings_created: number } | null>(null);
  const [matchStatus, setMatchStatus] = useState<"polling" | "success" | "timeout" | "idle">("idle");
  const [matchProgress, setMatchProgress] = useState(0);

  const pollIntervalRef = useRef<number | null>(null);

  const loadData = (activeToken: string) => {
    if (!activeToken) return;
    setError(null);
    setLoading(true);

    // We fetch `/people` and health endpoint to know privacy state
    // We also fetch the consent statement to show
    Promise.all([
      enrollmentApi.getPeople(activeToken),
      enrollmentApi.getConsent(),
      // Call /health on visiond via request to know privacy mode
      fetch(`http://${window.location.hostname}:8081/health`, {
        headers: { Authorization: `Bearer ${activeToken}` },
      }).then((r) => r.json()),
    ])
      .then(([p, c, h]) => {
        setPeople(p);
        setConsentStatement(c);
        setPrivacyEnabled(h.privacy_enabled ?? false);
      })
      .catch((err) => {
        setError(err.message || "Could not load enrollment data.");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (token) {
      loadData(token);
    }
  }, [token]);

  // Clean up poll on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) window.clearInterval(pollIntervalRef.current);
    };
  }, []);

  const saveToken = () => {
    window.localStorage.setItem(ADMIN_TOKEN_KEY, tokenInput);
    setToken(tokenInput);
  };

  const handlePrivacyToggle = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const targetVal = e.target.checked;
    try {
      setError(null);
      await enrollmentApi.setPrivacyMode(token, targetVal);
      setPrivacyEnabled(targetVal);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to toggle privacy mode.");
    }
  };

  const handleUnenroll = async (personId: string) => {
    try {
      setError(null);
      await enrollmentApi.unenroll(token, personId);
      setConfirmUnenrollId(null);
      loadData(token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unenrollment failed.");
    }
  };

  // Image capture
  const handleCapture = async () => {
    try {
      setError(null);
      const blob = await enrollmentApi.captureSnapshot(token);
      const url = URL.createObjectURL(blob);
      setCapturedBlobs((prev) => [...prev, blob]);
      setCapturedPreviews((prev) => [...prev, url]);
    } catch {
      setError("Camera snapshot capture failed. Falling back to mock capture.");
      // Fallback dummy blob for simulator/mock mode
      const mockBlob = new Blob([new Uint8Array(8)], { type: "image/jpeg" });
      const url = URL.createObjectURL(mockBlob);
      setCapturedBlobs((prev) => [...prev, mockBlob]);
      setCapturedPreviews((prev) => [...prev, url]);
    }
  };

  const submitEnrollment = async () => {
    if (capturedBlobs.length === 0) {
      setError("No captured images.");
      return;
    }
    setError(null);
    setLoading(true);

    const formData = new FormData();
    formData.append("display_name", displayName);
    formData.append("consent_version", consentStatement.version);
    formData.append("consent_confirmed", "true");
    formData.append("profile_id", profileId);
    formData.append("color", color);
    if (sound.trim()) {
      formData.append("sound", sound.trim());
    }

    capturedBlobs.forEach((blob, idx) => {
      formData.append("images", blob, `image_${idx}.jpg`);
    });

    try {
      const res = await enrollmentApi.enroll(token, formData);
      setEnrollResult(res);
      setWizardStep("match");
      startTestMatch(res.person_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Enrollment submission failed.");
    } finally {
      setLoading(false);
    }
  };

  const startTestMatch = (personId: string) => {
    setMatchStatus("polling");
    setMatchProgress(0);
    let attempts = 0;
    const maxAttempts = 20;

    if (pollIntervalRef.current) window.clearInterval(pollIntervalRef.current);

    pollIntervalRef.current = window.setInterval(async () => {
      attempts++;
      setMatchProgress((attempts / maxAttempts) * 100);

      try {
        const visitor = await enrollmentApi.getCurrentVisitor(token);
        if (visitor && visitor.person_id === personId) {
          setMatchStatus("success");
          if (pollIntervalRef.current) window.clearInterval(pollIntervalRef.current);
        }
      } catch {
        // ignore errors during poll
      }

      if (attempts >= maxAttempts && matchStatus !== "success") {
        setMatchStatus("timeout");
        if (pollIntervalRef.current) window.clearInterval(pollIntervalRef.current);
      }
    }, 500);
  };

  const openWizard = () => {
    setCapturedBlobs([]);
    setCapturedPreviews([]);
    setDisplayName("");
    setProfileId("blue_wave");
    setColor("#0000ff");
    setSound("");
    setConsentAgreed(false);
    setEnrollResult(null);
    setMatchStatus("idle");
    setWizardStep("consent");
    setShowWizard(true);
  };

  const closeWizard = () => {
    setShowWizard(false);
    if (pollIntervalRef.current) window.clearInterval(pollIntervalRef.current);
    loadData(token);
  };

  if (!token) {
    return (
      <div className="admin-social-panel">
        <h2>Face Recognition & Enrollment</h2>
        <p className="placeholder-subtext">Enter the admin token to unlock this panel.</p>
        <input
          type="password"
          value={tokenInput}
          onChange={(e) => setTokenInput(e.target.value)}
          placeholder="Admin token"
        />
        <button className="phrase-btn" onClick={saveToken}>Unlock</button>
      </div>
    );
  }

  const activeConfirmPerson = people.find((p) => p.person_id === confirmUnenrollId);

  return (
    <div className="admin-social-panel">
      <h2>Face Recognition & Enrollment</h2>
      {error && <p className="poll-error">{error}</p>}

      {/* Top controls */}
      <div className="my-content-row" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="privacy-toggle-container" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <label className="switch-label" style={{ fontWeight: "bold" }} htmlFor="privacy-mode-toggle">
            Privacy Mode:
          </label>
          <input
            type="checkbox"
            checked={privacyEnabled}
            onChange={handlePrivacyToggle}
            id="privacy-mode-toggle"
          />
          <span style={{ fontSize: "0.9rem", opacity: 0.8 }}>
            {privacyEnabled ? "Enabled (recognition stopped)" : "Disabled (recognition active)"}
          </span>
        </div>
        <button className="phrase-btn" onClick={openWizard} disabled={privacyEnabled}>
          Enroll New Face
        </button>
      </div>

      {privacyEnabled && (
        <div style={{ background: "#332211", borderLeft: "4px solid #ff9900", padding: "10px", margin: "10px 0", borderRadius: "4px" }}>
          <p style={{ margin: 0, fontSize: "0.9rem" }}>
            ⚠️ Privacy Mode is active. recognition is disabled at the source, and new enrollments are locked.
          </p>
        </div>
      )}

      {/* Enrolled people list */}
      <h3>Enrolled Members ({people.length})</h3>
      {people.length === 0 ? (
        <p className="placeholder-subtext">No members enrolled yet.</p>
      ) : (
        <div className="admin-recording-list">
          {people.map((person) => (
            <div
              key={person.person_id}
              className="admin-recording-row"
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "10px",
                borderBottom: "1px solid #333",
              }}
            >
              <div>
                <strong style={{ fontSize: "1.1rem" }}>{person.display_name}</strong>
                <div style={{ fontSize: "0.85rem", opacity: 0.7, marginTop: "4px" }}>
                  <span>ID: {person.person_id} | </span>
                  <span>Consent: {person.consent_version} ({person.consent_at.replace("T", " ").substring(0, 10)})</span>
                </div>
                <div style={{ display: "flex", gap: "10px", marginTop: "6px", alignItems: "center" }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: "12px",
                      height: "12px",
                      borderRadius: "50%",
                      backgroundColor: person.color,
                    }}
                  />
                  <span className="route-tag">{person.profile_id}</span>
                  {person.sound && <span style={{ opacity: 0.6 }}>🔊 {person.sound}</span>}
                </div>
              </div>
              <div>
                <button
                  className="delete-recording-btn"
                  onClick={() => setConfirmUnenrollId(person.person_id)}
                >
                  Unenroll
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Confirm Unenroll Dialog */}
      {confirmUnenrollId && activeConfirmPerson && (
        <div className="modal-overlay" style={{ position: "fixed", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(0,0,0,0.8)", display: "flex", justifyContent: "center", alignItems: "center", zIndex: 1000 }}>
          <div style={{ background: "#222", padding: "24px", borderRadius: "8px", maxWidth: "450px", width: "90%", border: "1px solid #444" }}>
            <h4 style={{ margin: "0 0 16px 0", color: "#ff4444" }}>Confirm Unenrollment</h4>
            <p>
              Are you sure you want to unenroll <strong>{activeConfirmPerson.display_name}</strong>?
            </p>
            <p style={{ color: "#ff8888", fontSize: "0.9rem" }}>
              ⚠️ Deletion of face templates is immediate and irreversible. All biometric records for this profile will be permanently wiped.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "12px", marginTop: "24px" }}>
              <button className="phrase-btn" onClick={() => setConfirmUnenrollId(null)}>
                Cancel
              </button>
              <button
                className="delete-recording-btn"
                style={{ float: "none" }}
                onClick={() => handleUnenroll(confirmUnenrollId)}
              >
                Delete Permanently
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Guided Enrollment Wizard */}
      {showWizard && (
        <div className="modal-overlay" style={{ position: "fixed", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(0,0,0,0.85)", display: "flex", justifyContent: "center", alignItems: "center", zIndex: 1000 }}>
          <div style={{ background: "#1a1a1a", padding: "30px", borderRadius: "10px", maxWidth: "600px", width: "95%", border: "1px solid #333", maxHeight: "90vh", overflowY: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
              <h3 style={{ margin: 0 }}>Face Enrollment Wizard</h3>
              <button
                style={{ background: "transparent", border: "none", color: "#888", fontSize: "1.5rem", cursor: "pointer" }}
                onClick={closeWizard}
              >
                &times;
              </button>
            </div>

            {/* Step 1: Consent */}
            {wizardStep === "consent" && (
              <div>
                <h4>Step 1: Consent Agreement ({consentStatement.version})</h4>
                <div
                  style={{
                    height: "200px",
                    overflowY: "auto",
                    background: "#111",
                    padding: "15px",
                    borderRadius: "6px",
                    border: "1px solid #222",
                    fontSize: "0.9rem",
                    lineHeight: "1.4",
                    marginBottom: "20px",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {consentStatement.text}
                </div>
                <label style={{ display: "flex", alignItems: "center", gap: "10px", cursor: "pointer", marginBottom: "24px" }}>
                  <input
                    type="checkbox"
                    checked={consentAgreed}
                    onChange={(e) => setConsentAgreed(e.target.checked)}
                  />
                  <span>I agree to the terms in the face-recognition consent statement verbatim.</span>
                </label>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button className="phrase-btn" onClick={closeWizard}>
                    Cancel
                  </button>
                  <button
                    className="phrase-btn"
                    disabled={!consentAgreed}
                    onClick={() => setWizardStep("capture")}
                  >
                    Next &rarr;
                  </button>
                </div>
              </div>
            )}

            {/* Step 2: Capture */}
            {wizardStep === "capture" && (
              <div>
                <h4>Step 2: Guided Photo Capture</h4>
                <div style={{ display: "flex", gap: "20px", marginBottom: "20px" }}>
                  <div style={{ flex: 1, height: "220px", background: "#000", borderRadius: "6px", overflow: "hidden" }}>
                    <LiveVideoPreview title="Live Stream View" />
                  </div>
                  <div style={{ width: "160px", display: "flex", flexDirection: "column", gap: "10px", justifyContent: "center" }}>
                    <div style={{ fontSize: "0.9rem", fontWeight: "bold" }}>
                      Captured: {capturedBlobs.length}/3
                    </div>
                    {capturedPreviews.map((src, i) => (
                      <img
                        key={i}
                        src={src}
                        alt={`Capture ${i + 1}`}
                        style={{ width: "100%", height: "45px", objectFit: "cover", borderRadius: "4px", border: "1px solid #444" }}
                      />
                    ))}
                  </div>
                </div>

                <div style={{ minHeight: "60px", marginBottom: "24px" }}>
                  {capturedBlobs.length === 0 && (
                    <p style={{ margin: 0 }}>📸 <strong>Pose 1:</strong> Look straight at the camera (center face).</p>
                  )}
                  {capturedBlobs.length === 1 && (
                    <p style={{ margin: 0 }}>📸 <strong>Pose 2:</strong> Turn your head slightly to the left.</p>
                  )}
                  {capturedBlobs.length === 2 && (
                    <p style={{ margin: 0 }}>📸 <strong>Pose 3:</strong> Turn your head slightly to the right.</p>
                  )}
                  {capturedBlobs.length >= 3 && (
                    <p style={{ margin: 0, color: "#44ff44" }}>✅ 3 captures completed successfully!</p>
                  )}
                </div>

                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <button className="phrase-btn" onClick={() => setWizardStep("consent")}>
                    &larr; Back
                  </button>
                  <div style={{ display: "flex", gap: "10px" }}>
                    {capturedBlobs.length < 3 ? (
                      <button className="phrase-btn" onClick={handleCapture}>
                        Capture Image {capturedBlobs.length + 1}
                      </button>
                    ) : (
                      <button className="phrase-btn" onClick={() => setWizardStep("profile")}>
                        Next &rarr;
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Step 3: Profile */}
            {wizardStep === "profile" && (
              <div>
                <h4>Step 3: Profile Configuration</h4>
                <div style={{ display: "flex", flexDirection: "column", gap: "16px", marginBottom: "24px" }}>
                  <div>
                    <label style={{ display: "block", marginBottom: "6px" }}>Display Name</label>
                    <input
                      type="text"
                      style={{ width: "100%", padding: "8px", background: "#222", border: "1px solid #333", borderRadius: "4px" }}
                      placeholder="e.g. Alice"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                    />
                  </div>
                  <div>
                    <label style={{ display: "block", marginBottom: "6px" }}>Profile ID (Effects Catalog)</label>
                    <select
                      value={profileId}
                      onChange={(e) => setProfileId(e.target.value)}
                      style={{ width: "100%", padding: "8px", background: "#222", border: "1px solid #333", borderRadius: "4px" }}
                    >
                      <option value="blue_wave">blue_wave</option>
                      <option value="sunrise">sunrise</option>
                      <option value="mint_pulse">mint_pulse</option>
                      <option value="green_pulse">green_pulse</option>
                      <option value="generic_press">generic_press</option>
                      <option value="fallback">fallback</option>
                    </select>
                  </div>
                  <div>
                    <label style={{ display: "block", marginBottom: "6px" }}>Accent Color</label>
                    <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                      <input
                        type="color"
                        value={color}
                        onChange={(e) => setColor(e.target.value)}
                        style={{ width: "50px", height: "35px", border: "none", borderRadius: "4px", background: "transparent", cursor: "pointer" }}
                      />
                      <input
                        type="text"
                        value={color}
                        onChange={(e) => setColor(e.target.value)}
                        style={{ flex: 1, padding: "8px", background: "#222", border: "1px solid #333", borderRadius: "4px" }}
                      />
                    </div>
                  </div>
                  <div>
                    <label style={{ display: "block", marginBottom: "6px" }}>Optional Sound ID</label>
                    <input
                      type="text"
                      style={{ width: "100%", padding: "8px", background: "#222", border: "1px solid #333", borderRadius: "4px" }}
                      placeholder="e.g. chime"
                      value={sound}
                      onChange={(e) => setSound(e.target.value)}
                    />
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <button className="phrase-btn" onClick={() => setWizardStep("capture")}>
                    &larr; Back
                  </button>
                  <button
                    className="phrase-btn"
                    disabled={!displayName.trim() || loading}
                    onClick={submitEnrollment}
                  >
                    {loading ? "Submitting..." : "Submit Enrollment"}
                  </button>
                </div>
              </div>
            )}

            {/* Step 4: Test Match */}
            {wizardStep === "match" && enrollResult && (
              <div>
                <h4>Step 4: Test Face Recognition Match</h4>
                <div style={{ textAlign: "center", padding: "20px 0" }}>
                  <p>
                    Enrollment complete! Generated ID: <code>{enrollResult.person_id}</code>
                  </p>
                  <p style={{ margin: "20px 0" }}>
                    Please show your face to the camera to verify matching.
                  </p>

                  <div style={{ width: "100%", height: "8px", background: "#333", borderRadius: "4px", overflow: "hidden", marginBottom: "20px" }}>
                    <div style={{ width: `${matchProgress}%`, height: "100%", background: "#00ff00", transition: "width 0.2s" }} />
                  </div>

                  {matchStatus === "polling" && (
                    <div style={{ color: "#ffaa00", fontWeight: "bold" }}>
                      🔍 Scanning and verifying... Please look at the camera.
                    </div>
                  )}

                  {matchStatus === "success" && (
                    <div style={{ color: "#44ff44", fontWeight: "bold", fontSize: "1.2rem" }}>
                      🎉 Match Successful! Profile recognized as {displayName}.
                    </div>
                  )}

                  {matchStatus === "timeout" && (
                    <div style={{ color: "#ff5555", fontWeight: "bold" }}>
                      ❌ Verification timed out. Your face wasn't detected.
                    </div>
                  )}
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "24px" }}>
                  <button className="phrase-btn" onClick={closeWizard}>
                    Finish & Close
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
