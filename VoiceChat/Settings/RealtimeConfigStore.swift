import Foundation

/// Non-secret Realtime API connection settings (endpoint + model), persisted via
/// UserDefaults. The API key itself lives in `APIKeyStore` (Keychain).
///
/// Defaults point at Qwen-Audio-Realtime, verified working against the generic
/// (non-workspace-scoped) DashScope endpoint on 2026-08-19: connected, session.created
/// came back, and `qwen-omni-turbo-realtime`'s session confirmed it honors
/// `turn_detection.create_response` and `interrupt_response` (see web-demo/README.md
/// for the raw test). Provider naming/endpoints in this space still change
/// frequently — re-verify if things stop working. To use OpenAI's Realtime API
/// instead, point `baseURL` at `wss://api.openai.com/v1/realtime`, `model` at
/// `gpt-realtime`, and change `inputWireFormat`'s sample rate in `AudioIOManager`
/// from 16kHz to 24kHz.
enum RealtimeConfigStore {
    private static let baseURLKey = "realtime.baseURL"
    private static let modelKey = "realtime.model"
    private static let voiceKey = "realtime.voice"

    static let defaultBaseURL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    static let defaultModel = "qwen-omni-turbo-realtime"
    static let defaultVoice = "Chelsie"

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
