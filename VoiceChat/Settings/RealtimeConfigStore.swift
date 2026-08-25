import Foundation

/// Non-secret Realtime API connection settings (endpoint + model), persisted via
/// UserDefaults. The API key itself lives in `APIKeyStore` (Keychain).
///
/// Defaults point at Qwen3.5-Omni-Realtime via the workspace-specific domain,
/// verified working on 2026-08-23 (full writeup: docs/qwen-realtime-voice-setup.md).
/// The older combo (shared `dashscope.aliyuncs.com` domain + `qwen-omni-turbo-realtime`,
/// used before that date) reproduced a multi-day `session.update` hang across dozens
/// of independent tests; switching to the workspace domain eliminated it in every
/// retest, including a 4-turn memory-grounding sequence that previously failed. Get
/// `workspaceId` from the 百炼 console (see doc above) — it's the Workspace ID, NOT
/// your account/主账号 UID, which looks similar but gets rejected with "Workspace
/// endpoint is invalid." Provider naming/endpoints in this space still change
/// frequently — re-verify if things stop working. To use OpenAI's Realtime API
/// instead, point `baseURL` at `wss://api.openai.com/v1/realtime`, `model` at
/// `gpt-realtime`, and change `inputWireFormat`'s sample rate in `AudioIOManager`
/// from 16kHz to 24kHz.
enum RealtimeConfigStore {
    private static let baseURLKey = "realtime.baseURL"
    private static let modelKey = "realtime.model"
    private static let voiceKey = "realtime.voice"
    private static let workspaceIdKey = "realtime.workspaceId"
    private static let vadThresholdKey = "realtime.vadThreshold"
    private static let vadSilenceDurationMsKey = "realtime.vadSilenceDurationMs"

    static let defaultBaseURL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    static let defaultModel = "qwen3.5-omni-flash-realtime"
    static let defaultVoice = "Serena"
    /// Same tuning history/parity note as the web port's TUNING_DEFAULTS (app.js) and
    /// RealtimeModels.swift's old hardcoded literals this replaced — see that file's
    /// turn_detection comment for the full 0.5→0.65→0.55 / 500→800→900 story.
    static let defaultVadThreshold = 0.55
    static let defaultVadSilenceDurationMs = 900

    /// Trimmed down to the requested shortlist (2026-08-23) — was a 14-voice curated
    /// subset of the ~47 Qwen3.5-Omni-Realtime supports (full list:
    /// https://help.aliyun.com/zh/model-studio/omni-voice-list), the rest parked for
    /// later. Every entry here has been individually verified via a session.update
    /// round-trip against qwen3.5-omni-flash-realtime + the workspace domain, not
    /// just taken from docs — 'Chelsie', the old default, isn't accepted by this
    /// model ("Voice 'Chelsie' is not supported."), so don't assume without testing.
    static let voiceOptions: [(id: String, label: String)] = [
        ("Serena", "Serena（女，温柔，默认）"),
        ("Tina", "Tina（女，甜美，官方默认）"),
        ("Sunnybobi", "Sunnybobi（女，大大咧咧的社恐邻家姑娘）"),
        ("Ethan", "Ethan（男，标准普通话）"),
        ("Raymond", "Raymond（男，清亮）"),
        ("Dylan", "Dylan（男，北京话）"),
    ]

    static var baseURL: String {
        get { UserDefaults.standard.string(forKey: baseURLKey) ?? defaultBaseURL }
        set { UserDefaults.standard.set(newValue, forKey: baseURLKey) }
    }

    static var model: String {
        get { UserDefaults.standard.string(forKey: modelKey) ?? defaultModel }
        set { UserDefaults.standard.set(newValue, forKey: modelKey) }
    }

    static var voice: String {
        get { UserDefaults.standard.string(forKey: voiceKey) ?? defaultVoice }
        set { UserDefaults.standard.set(newValue, forKey: voiceKey) }
    }

    /// Not a secret (it's not a credential — the API key is what authenticates), but
    /// still just an identifier, so plain UserDefaults like the rest of this store.
    static var workspaceId: String {
        get { UserDefaults.standard.string(forKey: workspaceIdKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: workspaceIdKey) }
    }

    /// Server VAD sensitivity (0–1, lower = more readily hears "someone's talking").
    /// See RealtimeModels.swift's turn_detection comment for the full tuning history.
    static var vadThreshold: Double {
        get {
            let stored = UserDefaults.standard.double(forKey: vadThresholdKey)
            return stored == 0 ? defaultVadThreshold : stored
        }
        set { UserDefaults.standard.set(newValue, forKey: vadThresholdKey) }
    }

    /// How long the server waits in silence before treating the user's turn as over.
    static var vadSilenceDurationMs: Int {
        get {
            let stored = UserDefaults.standard.integer(forKey: vadSilenceDurationMsKey)
            return stored == 0 ? defaultVadSilenceDurationMs : stored
        }
        set { UserDefaults.standard.set(newValue, forKey: vadSilenceDurationMsKey) }
    }

    /// The URL actually used to connect: when `workspaceId` is set, it overrides
    /// `baseURL` with the workspace-specific domain (the fix for the hang described
    /// above); otherwise falls back to whatever `baseURL` holds (shared domain by
    /// default, or anything the user typed in Settings, e.g. an OpenAI endpoint).
    static var effectiveBaseURL: String {
        let id = sanitizedWorkspaceId(workspaceId)
        guard !id.isEmpty else { return baseURL }
        return effectiveWorkspaceURL(for: id)
    }

    /// Same derivation as `effectiveBaseURL`, but for a not-yet-saved ID string — lets
    /// Settings preview the resulting URL as the user types, before hitting Save.
    static func effectiveWorkspaceURL(for workspaceId: String) -> String {
        "wss://\(sanitizedWorkspaceId(workspaceId)).cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
    }

    /// Compatible-mode (plain HTTPS text completions, not the Realtime WS) base URL —
    /// used only by `DictationCleanupClient`. Same workspace-domain-when-set,
    /// shared-domain-fallback pattern as `effectiveBaseURL`.
    static var effectiveCompatibleModeBaseURL: String {
        let id = sanitizedWorkspaceId(workspaceId)
        if !id.isEmpty {
            return "https://\(id).cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        }
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    }

    /// Real-world bug, not hypothetical (2026-08-24): a user's pasted workspace ID
    /// carried a stray trailing backtick into the URL (` wss://llm-xxxx`.cn-beijing...`),
    /// almost certainly picked up from copying the ID out of somewhere it was wrapped in
    /// Markdown inline-code backticks (a doc, a chat message) and only one side coming
    /// along. `workspaceId` is free-typed/pasted in Settings, so beyond whitespace,
    /// strip the handful of characters that are never legitimately part of an
    /// alphanumeric workspace ID but are exactly what a copy-paste from formatted text
    /// leaves behind: backticks and straight/curly quotes.
    private static func sanitizedWorkspaceId(_ raw: String) -> String {
        let strayCharacters = CharacterSet(charactersIn: "`'\"\u{2018}\u{2019}\u{201C}\u{201D}")
        return raw
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .components(separatedBy: strayCharacters)
            .joined()
    }
}
