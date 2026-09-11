/** Shared pill switcher between Qwen Realtime (8765) and LiveKit + Pipecat (8766). */
(function () {
  const MODES = {
    qwen: {
      label: "Qwen Realtime",
      url: "http://127.0.0.1:8765/",
    },
    livekit: {
      label: "LiveKit + Pipecat",
      url: "http://127.0.0.1:8766/",
    },
  };

  function detectCurrentMode() {
    return location.port === "8766" ? "livekit" : "qwen";
  }

  function mountSwitcher(container, { currentMode, onBeforeLeave } = {}) {
    if (!container) return;

    const activeMode = currentMode || detectCurrentMode();
    container.innerHTML = `
      <div class="modeSwitcher" role="tablist" aria-label="语音方案切换">
        <button type="button" class="modeSwitcherBtn" data-mode="qwen" role="tab">Qwen Realtime</button>
        <button type="button" class="modeSwitcherBtn" data-mode="livekit" role="tab">LiveKit + Pipecat</button>
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
          await onBeforeLeave(target);
        }
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
