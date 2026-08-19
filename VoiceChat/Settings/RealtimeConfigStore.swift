import Foundation

/// Non-secret Realtime API connection settings (endpoint + model), persisted via
/// UserDefaults. The API key itself lives in `APIKeyStore` (Keychain).
///
/// Defaults point at Qwen-Audio-Realtime. Provider naming/endpoints in this space
/// change frequently — verify the current values in your DashScope/Model Studio
/// console before relying on the defaults. To use OpenAI's Realtime API instead,
/// point `baseURL` at `wss://api.openai.com/v1/realtime`, `model` at `gpt-realtime`,
/// and change `inputWireFormat`'s sample rate in `AudioIOManager` from 16kHz to 24kHz.
enum RealtimeConfigStore {
    private static let baseURLKey = "realtime.baseURL"
    private static let modelKey = "realtime.model"
    private static let voiceKey = "realtime.voice"

    static let defaultBaseURL = "wss://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
    static let defaultModel = "qwen-audio-3.0-realtime-plus"
    static let defaultVoice = "Cherry"

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
}
