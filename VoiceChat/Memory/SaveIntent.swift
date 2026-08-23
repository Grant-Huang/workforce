import Foundation

/// Detects an explicit "remember this" intent in what the user said, so it can be
/// written into AgentNexus's curated memory layers (ANCHOR/DECISIONS/PROGRESS)
/// instead of just flowing into the raw message history like everything else.
///
/// Deliberately simple (keyword trigger + strip it out of the sentence) — mirrors
/// web-demo/static/saveIntent.js, validated there first. See
/// docs/agentnexus-memory-integration-proposal.md's "raw dialogue vs curated memory"
/// split for why this distinction matters.
struct SaveIntent {
    let trigger: String
    let content: String

    private static let triggers = ["记住", "帮我记一下", "帮我记下", "帮我记住", "提醒我", "别忘了", "记一下"]

    static func detect(_ text: String) -> SaveIntent? {
        for trigger in triggers {
            guard let range = text.range(of: trigger) else { continue }
            var content = text
            content.removeSubrange(range)
            content = content.trimmingCharacters(in: CharacterSet(charactersIn: "，,。.！! \n\t"))
            if content.isEmpty { content = text } // trigger was the whole utterance
            return SaveIntent(trigger: trigger, content: content)
        }
        return nil
    }
}
