import React, { useEffect, useState } from "react";
import { socialApi, ApiError } from "./socialApi";
import type {
  GuestbookEntry,
  Poll,
  MostFrequentVisitorStat,
  ModerationLogEntry,
  ScoreboardEntry,
  FoodRecommendation,
} from "./socialApi";

// Guestbook/poll/check-in moderation panel (T-403). Gated by door-api's
// placeholder admin-token bearer auth (see door_api/social/routes.py) —
// this is a stopgap until packages/auth ships real admin sessions. The
// token is entered once and cached in localStorage for this browser only;
// it is never linked from any public route.

const ADMIN_TOKEN_KEY = "doorboard_admin_social_token";

function apiErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

export function AdminSocialPanel() {
  const [token, setToken] = useState<string>(
    () => window.localStorage.getItem(ADMIN_TOKEN_KEY) || ""
  );
  const [tokenInput, setTokenInput] = useState(token);
  const [error, setError] = useState<string | null>(null);

  const [pending, setPending] = useState<GuestbookEntry[]>([]);
  const [approved, setApproved] = useState<GuestbookEntry[]>([]);
  const [polls, setPolls] = useState<Poll[]>([]);
  const [stat, setStat] = useState<MostFrequentVisitorStat | null>(null);
  const [log, setLog] = useState<ModerationLogEntry[]>([]);

  // Mood/Scoreboard/Food States
  const [moods, setMoods] = useState<Record<string, string>>({});
  const [boards, setBoards] = useState<Record<string, ScoreboardEntry[]>>({});
  const [food, setFood] = useState<FoodRecommendation | null>(null);

  // Scoreboard Form States
  const [boardId, setBoardId] = useState("daily");
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [score, setScore] = useState(0);

  // Scoreboard Editing States
  const [editingEntryId, setEditingEntryId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [editingNotes, setEditingNotes] = useState("");
  const [editingScore, setEditingScore] = useState(0);

  const [newQuestion, setNewQuestion] = useState("");
  const [newOptions, setNewOptions] = useState(["", ""]);

  const loadAll = (activeToken: string) => {
    if (!activeToken) return;
    setError(null);
    Promise.all([
      socialApi.admin.listGuestbook("pending", activeToken),
      socialApi.admin.listGuestbook("approved", activeToken),
      socialApi.admin.listPolls(activeToken),
      socialApi.mostFrequentVisitor(),
      socialApi.admin.moderationLog(activeToken),
      socialApi.getCurrentMoods(),
      socialApi.getScoreboard(),
      socialApi.getLatestFood(),
    ])
      .then(([p, a, polledPolls, s, l, md, sb, fd]) => {
        setPending(p);
        setApproved(a);
        setPolls(polledPolls);
        setStat(s);
        setLog(l);
        setMoods(md);
        setBoards(sb.boards);
        setFood(fd);
      })
      .catch((err) => setError(apiErrorMessage(err, "Couldn't load the moderation panel.")));
  };

  useEffect(() => {
    if (token) loadAll(token);
  }, [token]);

  const saveToken = () => {
    window.localStorage.setItem(ADMIN_TOKEN_KEY, tokenInput);
    setToken(tokenInput);
  };

  const approve = (id: string) => {
    socialApi.admin
      .approveGuestbook(id, token)
      .then(() => loadAll(token))
      .catch((err) => setError(apiErrorMessage(err, "Couldn't approve that entry.")));
  };

  const remove = (id: string) => {
    socialApi.admin
      .deleteGuestbook(id, token)
      .then(() => loadAll(token))
      .catch((err) => setError(apiErrorMessage(err, "Couldn't delete that entry.")));
  };

  const createPoll = () => {
    const cleanOptions = newOptions.map((o) => o.trim()).filter(Boolean);
    if (newQuestion.trim().length === 0 || cleanOptions.length < 2) {
      setError("A poll needs a question and at least 2 options.");
      return;
    }
    socialApi.admin
      .createPoll(newQuestion.trim(), cleanOptions, token)
      .then(() => {
        setNewQuestion("");
        setNewOptions(["", ""]);
        loadAll(token);
      })
      .catch((err) => setError(apiErrorMessage(err, "Couldn't create the poll.")));
  };

  const closePoll = (id: string) => {
    socialApi.admin
      .closePoll(id, token)
      .then(() => loadAll(token))
      .catch((err) => setError(apiErrorMessage(err, "Couldn't close that poll.")));
  };

  const changeMood = (subjectId: string, mood: string) => {
    socialApi.admin
      .updateMood(subjectId, mood, token)
      .then(() => loadAll(token))
      .catch((err) => setError(apiErrorMessage(err, "Failed to update mood")));
  };

  const createScoreboardEntry = () => {
    if (!title.trim()) {
      setError("Title/participant is required.");
      return;
    }
    socialApi.admin
      .createScoreboardEntry(boardId.trim(), title.trim(), notes.trim() || null, score, token)
      .then(() => {
        setTitle("");
        setNotes("");
        setScore(0);
        loadAll(token);
      })
      .catch((err) => setError(apiErrorMessage(err, "Failed to create scoreboard entry")));
  };

  const adjustScore = (entry: ScoreboardEntry, diff: number) => {
    const newScore = entry.score + diff;
    socialApi.admin
      .updateScoreboardEntry(entry.entry_id, entry.title, entry.notes, newScore, token)
      .then(() => loadAll(token))
      .catch((err) => setError(apiErrorMessage(err, "Failed to adjust score")));
  };

  const saveScoreboardEntry = (entryId: string) => {
    socialApi.admin
      .updateScoreboardEntry(
        entryId,
        editingTitle.trim(),
        editingNotes.trim() || null,
        editingScore,
        token
      )
      .then(() => {
        setEditingEntryId(null);
        loadAll(token);
      })
      .catch((err) => setError(apiErrorMessage(err, "Failed to save entry")));
  };

  const deleteScoreboardEntry = (entryId: string) => {
    socialApi.admin
      .deleteScoreboardEntry(entryId, token)
      .then(() => loadAll(token))
      .catch((err) => setError(apiErrorMessage(err, "Failed to delete entry")));
  };

  const startEditing = (entry: ScoreboardEntry) => {
    setEditingEntryId(entry.entry_id);
    setEditingTitle(entry.title);
    setEditingNotes(entry.notes || "");
    setEditingScore(entry.score);
  };

  if (!token) {
    return (
      <div className="admin-social-panel">
        <h2>Guestbook / Poll / Check-in Moderation</h2>
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

  return (
    <div className="admin-social-panel">
      <h2>Guestbook / Poll / Check-in Moderation</h2>
      {error && <p className="poll-error">{error}</p>}

      <h3>Pending guestbook entries ({pending.length})</h3>
      {pending.map((entry) => (
        <div key={entry.id} className="my-content-row">
          <span>{entry.text}</span>
          <div>
            <button className="phrase-btn" onClick={() => approve(entry.id)}>Approve</button>
            <button className="phrase-btn" onClick={() => remove(entry.id)}>Delete</button>
          </div>
        </div>
      ))}
      {pending.length === 0 && <p className="placeholder-subtext">Nothing pending.</p>}

      <h3>Approved guestbook entries ({approved.length})</h3>
      {approved.map((entry) => (
        <div key={entry.id} className="my-content-row">
          <span>{entry.text}</span>
          <button className="phrase-btn" onClick={() => remove(entry.id)}>Delete</button>
        </div>
      ))}

      <h3>Polls</h3>
      <div className="admin-poll-create">
        <input
          type="text"
          placeholder="Poll question"
          value={newQuestion}
          onChange={(e) => setNewQuestion(e.target.value)}
        />
        {newOptions.map((opt, idx) => (
          <input
            key={idx}
            type="text"
            placeholder={`Option ${idx + 1}`}
            value={opt}
            onChange={(e) => {
              const next = [...newOptions];
              next[idx] = e.target.value;
              setNewOptions(next);
            }}
          />
        ))}
        <button className="phrase-btn" onClick={() => setNewOptions([...newOptions, ""])}>
          + Option
        </button>
        <button className="phrase-btn" onClick={createPoll}>Create poll</button>
      </div>
      {polls.map((p) => (
        <div key={p.id} className="my-content-row">
          <span>{p.question} ({p.status})</span>
          {p.status === "open" && (
            <button className="phrase-btn" onClick={() => closePoll(p.id)}>Close</button>
          )}
        </div>
      ))}

      <h3>Most frequent visitor</h3>
      {stat ? (
        <p>{stat.label ?? stat.person_id} has checked in {stat.count} times! 🎉</p>
      ) : (
        <p className="placeholder-subtext">No enrolled check-ins yet.</p>
      )}

      <h3>Moods</h3>
      <div className="admin-mood-section" style={{ marginBottom: "16px" }}>
        {["owner", "roommate"].map((subj) => (
          <div key={subj} className="my-content-row" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
            <span style={{ textTransform: "capitalize" }}><strong>{subj}:</strong> {moods[subj] || "None"}</span>
            <div>
              {["focused", "chilling", "busy", "away"].map((m) => (
                <button
                  key={m}
                  className="phrase-btn"
                  style={{ marginRight: "4px", opacity: moods[subj] === m ? 0.6 : 1 }}
                  onClick={() => changeMood(subj, m)}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <h3>Scoreboards</h3>
      <div className="admin-scoreboard-section" style={{ marginBottom: "16px" }}>
        <h4 style={{ margin: "8px 0" }}>Create Entry</h4>
        <div className="admin-poll-create" style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "16px" }}>
          <input
            type="text"
            placeholder="Board ID (e.g. daily, foosball)"
            value={boardId}
            onChange={(e) => setBoardId(e.target.value)}
          />
          <input
            type="text"
            placeholder="Participant / Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <input
            type="text"
            placeholder="Notes (Casual Bet)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
          <input
            type="number"
            placeholder="Initial Score"
            value={score}
            onChange={(e) => setScore(parseInt(e.target.value) || 0)}
          />
          <button className="phrase-btn" onClick={createScoreboardEntry}>Create Entry</button>
        </div>

        {Object.entries(boards).map(([bId, entries]) => (
          <div key={bId} style={{ marginBottom: "16px" }}>
            <h4 style={{ margin: "8px 0", borderBottom: "1px solid #444", paddingBottom: "4px" }}>Board: {bId}</h4>
            {entries.map((entry) => (
              <div key={entry.entry_id} className="my-content-row" style={{ display: "flex", flexDirection: "column", padding: "8px", border: "1px solid #444", borderRadius: "4px", marginBottom: "8px" }}>
                {editingEntryId === entry.entry_id ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px", width: "100%" }}>
                    <input
                      type="text"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                    />
                    <input
                      type="text"
                      value={editingNotes}
                      onChange={(e) => setEditingNotes(e.target.value)}
                    />
                    <input
                      type="number"
                      value={editingScore}
                      onChange={(e) => setEditingScore(parseInt(e.target.value) || 0)}
                    />
                    <div style={{ display: "flex", gap: "8px" }}>
                      <button className="phrase-btn" onClick={() => saveScoreboardEntry(entry.entry_id)}>Save</button>
                      <button className="phrase-btn" onClick={() => setEditingEntryId(null)}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div style={{ display: "flex", justifyContent: "space-between", width: "100%", alignItems: "center" }}>
                    <div>
                      <strong>{entry.title}</strong>: {entry.score}
                      {entry.notes && <p style={{ margin: "4px 0", fontSize: "14px", color: "#aaa" }}>{entry.notes}</p>}
                    </div>
                    <div style={{ display: "flex", gap: "4px" }}>
                      <button className="phrase-btn" onClick={() => adjustScore(entry, 1)}>+1</button>
                      <button className="phrase-btn" onClick={() => adjustScore(entry, -1)}>-1</button>
                      <button className="phrase-btn" onClick={() => startEditing(entry)}>Edit</button>
                      <button className="phrase-btn" onClick={() => deleteScoreboardEntry(entry.entry_id)}>Delete</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>

      <h3>Food Recommendation</h3>
      <div className="admin-food-section" style={{ marginBottom: "16px", padding: "8px", border: "1px solid #444", borderRadius: "4px" }}>
        {food ? (
          <div>
            <p style={{ margin: "4px 0" }}><strong>Today's Pick:</strong> {food.title}</p>
            {food.detail && <p style={{ margin: "4px 0", color: "#aaa", fontSize: "14px" }}>{food.detail}</p>}
            <p style={{ margin: "4px 0", fontSize: "12px", color: "#888" }}>Date: {food.date}</p>
          </div>
        ) : (
          <p className="placeholder-subtext" style={{ margin: 0 }}>No food recommendation loaded yet.</p>
        )}
      </div>

      <h3>Moderation log</h3>
      {log.map((entry) => (
        <div key={entry.id} className="moderation-log-row">
          {entry.created_at} — {entry.actor} {entry.action} {entry.target_kind} {entry.target_id}
        </div>
      ))}
    </div>
  );
}
