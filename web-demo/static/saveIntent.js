// Detects an explicit "remember this" intent in what the user said, so it can be
// written into AgentNexus's curated memory layers (ANCHOR/DECISIONS/PROGRESS)
// instead of just flowing into the raw message history like everything else.
//
// Deliberately simple (keyword trigger + strip it out of the sentence) — see
// docs/agentnexus-memory-integration-proposal.md's "raw dialogue vs curated
// memory" split for why this distinction matters instead of writing every
// utterance into the structured layers.
const SaveIntent = (() => {
  const TRIGGERS = ["记住", "帮我记一下", "帮我记下", "帮我记住", "提醒我", "别忘了", "记一下"];

  /** @returns {{trigger: string, content: string} | null} */
  function detect(text) {
    for (const trigger of TRIGGERS) {
      const idx = text.indexOf(trigger);
      if (idx === -1) continue;
      let content = (text.slice(0, idx) + text.slice(idx + trigger.length))
        .replace(/^[，,。.！!\s]+|[，,。.！!\s]+$/g, "")
        .trim();
      if (!content) content = text; // trigger was the whole utterance -- nothing left to extract
      return { trigger, content };
    }
    return null;
  }

  return { detect };
})();
