/** Shared pill switcher for the unified `workforce.inkpath.cc` server (port 8787).
 *
 * Single-domain mode dispatch — toggles `?mode=` query instead of cross-domain nav,
 * so the audio session (WebSocket for Qwen Realtime / LiveKit room for Pipecat)
 * tears down gracefully via `onBeforeLeave` and the new mode spins up in the same tab.
 *
 * Three modes:
 *   qwen       — Qwen3.5-Omni-Realtime cloud WebSocket (default)
 *   livekit    — LiveKit Cloud + Mac Pipecat bot (compare mode)
 *   localAgent — LiveKit Cloud + Mac Pipecat bot, manual agent control (?ui=local)
 */
(function () {
  function targetFor(mode) {
    if (mode === "qwen") return "/";
    if (mode === "livekit") return "/?mode=livekit";
    if (mode === "localAgent") return "/?mode=livekit&ui=local";
    return "/";
  }

  const MODES = {
    qwen: { label: "Qwen Realtime", url: targetFor("qwen") },
    livekit: { label: "LiveKit + Pipecat", url: targetFor("livekit") },
    localAgent: { label: "Mac 本地 Agent", url: targetFor("localAgent") },
  };

  function detectCurrentMode() {
    const params = new URLSearchParams(location.search);
    if (params.get("mode") === "livekit" && params.get("ui") === "local") return "localAgent";
    if (params.get("mode") === "livekit") return "livekit";
    return "qwen";
  }

  function mountSwitcher(container, { currentMode, onBeforeLeave } = {}) {
    if (!container) return;

    const activeMode = currentMode || detectCurrentMode();
    container.innerHTML = `
      <div class="modeSwitcher" role="tablist" aria-label="语音方案切换">
        <button type="button" class="modeSwitcherBtn" data-mode="qwen" role="tab">Qwen Realtime</button>
        <button type="button" class="modeSwitcherBtn" data-mode="livekit" role="tab">LiveKit + Pipecat</button>
        <button type="button" class="modeSwitcherBtn" data-mode="localAgent" role="tab">Mac 本地 Agent</button>
      </div>
    `;

    container.querySelectorAll(".modeSwitcherBtn").forEach((btn) => {
      const isActive = btn.dataset.mode === activeMode;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
      btn.disabled = isActive;

      btn.addEventListener("click", async () => {
        const target = btn.dataset.mode;
        if (target === activeMode) return;
        if (typeof onBeforeLeave === "function") {
          try {
            await onBeforeLeave(target);
          } catch (e) {
            console.warn("onBeforeLeave failed, navigating anyway:", e);
          }
        }
        // In-page navigation (single-domain, no cross-origin) — preserves document.cookie
        // and sessionStorage, just reloads the right mode page.
        location.href = MODES[target].url;
      });
    });
  }

  window.VoiceModeSwitcher = {
    MODES,
    detectCurrentMode,
    mountSwitcher,
  };
})();
