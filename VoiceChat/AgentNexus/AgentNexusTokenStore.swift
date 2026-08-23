import Foundation
import Security

/// Keychain wrapper for the AgentNexus long-lived personal token (`pt_...`).
///
/// Same shape as `APIKeyStore` but a separate Keychain entry — this is a different
/// credential for a different service. Works today with a short-lived login JWT too
/// (paste one, re-paste when it expires); once AgentNexus's `get_current_user` accepts
/// `pt_...` tokens (see docs/agentnexus-memory-integration-proposal.md §2), nothing
/// on this side needs to change — it's just a bearer token either way.
enum AgentNexusTokenStore {
    private static let service = "com.jacer.voicechat.agentnexus"
    private static let account = "personal-token"

    static func save(_ token: String) {
        let data = Data(token.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)

        var attributes = query
        attributes[kSecValueData as String] = data
        SecItemAdd(attributes as CFDictionary, nil)
    }

    static func load() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func clear() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
