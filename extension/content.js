/*
 * OneShot Copilot — content script
 *
 * Reads the currently focused input/textarea on the page and, when the user
 * clicks the extension popup button, sends the question text to the local
 * OneShot server at http://127.0.0.1:5001/api/copilot/answer.
 *
 * No server-side form interaction. The user's own browser session submits.
 */

const ONESHOT_HOST = "http://127.0.0.1:5001";

// ── Focused-element capture ───────────────────────────────────────────────────

let _lastFocusedText = "";

document.addEventListener("focusin", (e) => {
  const el = e.target;
  if (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && el.type === "text")) {
    const label = _labelFor(el);
    if (label) _lastFocusedText = label;
  }
}, true);

function _labelFor(el) {
  // 1. <label for="id">
  if (el.id) {
    const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (lbl) return lbl.textContent.trim();
  }
  // 2. ancestor <label>
  const ancestor = el.closest("label");
  if (ancestor) return ancestor.textContent.trim();
  // 3. aria-label / aria-labelledby
  if (el.getAttribute("aria-label")) return el.getAttribute("aria-label").trim();
  const lblId = el.getAttribute("aria-labelledby");
  if (lblId) {
    const ref = document.getElementById(lblId);
    if (ref) return ref.textContent.trim();
  }
  // 4. placeholder as fallback
  return el.placeholder || "";
}

// ── Message listener (from popup) ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "GET_FOCUSED_QUESTION") {
    sendResponse({ question: _lastFocusedText });
    return true;
  }
  if (msg.type === "ASK_COPILOT") {
    const { question, job_id } = msg;
    fetch(`${ONESHOT_HOST}/api/copilot/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, job_id: job_id || "" }),
    })
      .then(r => r.json())
      .then(d => sendResponse({ ok: true, data: d }))
      .catch(e => sendResponse({ ok: false, error: String(e) }));
    return true; // keep channel open for async response
  }
});
