import Foundation

/// Non-secret AgentNexus connection settings (server address + channel), persisted via
/// UserDefaults. The token itself lives in `AgentNexusTokenStore` (Keychain).
///
/// `channelID` is the user's personal channel in AgentNexus (their 1:1 DM with the
/// "epicwise" personal assistant) — that's the memory scope this app reads/writes.
/// See docs/agentnexus-memory-integration-proposal.md for why it's per-channel today
/// and how that might grow to include cross-channel memory later.
enum AgentNexusConfigStore {
    private static let baseURLKey = "agentnexus.baseURL"
    private static let channelIDKey = "agentnexus.channelID"

    static let defaultBaseURL = ""
    static let defaultChannelID = ""

    static var baseURL: String {
        get { UserDefaults.standard.string(forKey: baseURLKey) ?? defaultBaseURL }
        set { UserDefaults.standard.set(newValue, forKey: baseURLKey) }
    }

    static var channelID: String {
        get { UserDefaults.standard.string(forKey: channelIDKey) ?? defaultChannelID }
        set { UserDefaults.standard.set(newValue, forKey: channelIDKey) }
    }

    static var isConfigured: Bool {
        !baseURL.isEmpty && !channelID.isEmpty && AgentNexusTokenStore.load()?.isEmpty == false
    }
}
