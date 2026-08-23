import Foundation

enum DictationCleanupError: LocalizedError {
    case noAPIKey
    case badResponse(String)

    var errorDescription: String? {
        switch self {
        case .noAPIKey: return "请先在设置里填入 API Key"
        case .badResponse(let message): return message
        }
    }
}

/// The AI-cleanup step for voice-dictate-to-text (docs/app-design.md section 3.2):
/// a plain one-shot text completion, deliberately separate from the Realtime WS
/// session that captured the raw transcript (DictationViewModel) — trying to coerce
/// the Realtime protocol into a text-only cleanup mode was considered and rejected in
/// the design doc as an unnecessary risk given how many Realtime-specific quirks this
/// app has already hit. Mirrors web-demo/server.py's `/api/dictation-cleanup` route:
/// same prompt, same model choice, verified there via Playwright with injected
/// stand-in transcripts (this Swift port itself is unverified — see
/// docs/testing-deployment.md's testing-coverage note).
enum DictationCleanupClient {
    private static let model = "qwen3.5-flash"
    private static let prompt = """
        把用户口述的这段话，整理成一段通顺、结构清晰的书面文字。\
        去掉口语里的语气词、重复、停顿词（"呃""就是""然后""那个"这些），\
        如果用户说话时想到哪说到哪、顺序乱，帮TA理顺逻辑顺序，\
        但不要添加原话里没有的信息，不要过度概括丢失细节，保持第一人称语气，\
        不要用列表/编号这种书面格式（除非原话本身就是在列举好几件事）。\
        直接给出整理后的文字，不要加任何前缀说明。
        """

    /// Measured latency (web demo, 2026-08-23): highly variable, 17-35+ seconds
    /// observed across several calls, once exceeding a 30s timeout. Callers must show
    /// a "整理中…" wait state and not assume this is fast — the 65s request timeout
    /// here matches the client-side backstop the web version added after hitting that
    /// variance directly.
    static func cleanup(_ rawText: String) async throws -> String {
        guard let apiKey = APIKeyStore.load(), !apiKey.isEmpty else {
            throw DictationCleanupError.noAPIKey
        }
        guard let url = URL(string: "\(RealtimeConfigStore.effectiveCompatibleModeBaseURL)/chat/completions") else {
            throw DictationCleanupError.badResponse("invalid cleanup URL")
        }

        var request = URLRequest(url: url, timeoutInterval: 65)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": model,
            "messages": [
                ["role": "system", "content": prompt],
                ["role": "user", "content": rawText],
            ],
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw DictationCleanupError.badResponse("cleanup call failed (HTTP \(status))")
        }
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = json["choices"] as? [[String: Any]],
              let message = choices.first?["message"] as? [String: Any],
              let content = message["content"] as? String else {
            throw DictationCleanupError.badResponse("unexpected cleanup response shape")
        }
        return content.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
