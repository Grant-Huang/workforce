import Foundation
import Security

/// Minimal Keychain wrapper for storing the user's OpenAI API key on-device.
///
/// This is a prototype-grade approach: the raw API key lives on the device and is sent
/// directly from the app to OpenAI's WebSocket endpoint. That's fine for personal use,
/// but before shipping to the App Store, replace this with a backend that mints
/// short-lived "ephemeral" Realtime session tokens (POST /v1/realtime/sessions) so the
/// long-lived API key never leaves your server.
enum APIKeyStore {
    private static let service = "com.jacer.voicechat.openai"
    private static let account = "api-key"

    static func save(_ key: String) {
        let data = Data(key.utf8)
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
