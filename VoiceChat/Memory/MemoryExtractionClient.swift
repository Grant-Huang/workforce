import Foundation

struct ExtractedFact {
    let text: String
    let isJargon: Bool
}

enum MemoryExtractionError: LocalizedError {
    case noAPIKey
    case badResponse(String)

    var errorDescription: String? {
        switch self {
        case .noAPIKey: return "请先在设置里填入 API Key"
        case .badResponse(let message): return message
        }
    }
}

/// Memory extraction (docs/app-design.md 7.3): same one-shot qwen-turbo mechanism as
/// `DictationCleanupClient`, different prompt — distills a completed user+assistant
/// turn into 0+ standalone facts worth remembering, run async (off the critical path)
/// once the reply is already showing. Mirrors web-demo/server.py's `/api/memory-extract`
/// route — same prompt, same `response_format: json_object` approach, empirically
/// verified there live against qwen-turbo (2026-08-24): reliably returns parseable
/// JSON, correctly tells jargon explanations apart from plain facts, returns an empty
/// list for a trivial turn, and correctly skips re-extracting an already-known jargon
/// term. This Swift port itself is unverified — no toolchain in this environment.
enum MemoryExtractionClient {
    private static let model = "qwen-turbo"

    private static func systemPrompt(knownJargon: [String]) -> String {
        let jargonList = knownJargon.isEmpty ? "（无）" : knownJargon.joined(separator: "、")
        return """
            你是一个记忆提炼助手。给定用户和助手在一轮对话里说的话，判断这轮对话里有没有值得长期记住的\
            事实性内容——比如用户的偏好、计划、决定、个人信息，或者用户解释了一个团队/个人黑话、术语的含义。

            如果有，用简洁清楚的第一人称转述提炼成 0 条到多条独立的事实（每条一两句话），不要逐字复述原话，\
            也不要加原话里没有的信息。如果这轮只是打招呼、闲聊、追问细节但没有新信息，返回空列表。

            如果某条事实是在解释一个黑话/术语的含义（"我们说的 XX 意思是 YY"这种），把 isJargon 设为 \
            true；其他普通事实设为 false。

            已知的黑话/术语（避免重复提炼这些已经记录过的）：
            \(jargonList)

            严格按以下 JSON 格式输出，不要有任何其他文字，不要用 markdown 代码块包裹：
            {"facts": [{"text": "...", "isJargon": false}]}
            没有值得记的内容时：{"facts": []}
            """
    }

    static func extract(userText: String, assistantText: String, knownJargon: [String]) async throws -> [ExtractedFact] {
        guard let apiKey = APIKeyStore.load(), !apiKey.isEmpty else {
            throw MemoryExtractionError.noAPIKey
        }
        guard let url = URL(string: "\(RealtimeConfigStore.effectiveCompatibleModeBaseURL)/chat/completions") else {
            throw MemoryExtractionError.badResponse("invalid extract URL")
        }

        var request = URLRequest(url: url, timeoutInterval: 65)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "model": model,
            "messages": [
                ["role": "system", "content": systemPrompt(knownJargon: knownJargon)],
                ["role": "user", "content": "用户说：\(userText)\n助手回复：\(assistantText)"],
            ],
            "response_format": ["type": "json_object"],
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw MemoryExtractionError.badResponse("extract call failed (HTTP \(status))")
        }
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = json["choices"] as? [[String: Any]],
              let message = choices.first?["message"] as? [String: Any],
              let content = message["content"] as? String,
              let contentData = content.data(using: .utf8) else {
            throw MemoryExtractionError.badResponse("unexpected extract response shape")
        }
        guard let parsed = try? JSONSerialization.jsonObject(with: contentData) as? [String: Any],
              let facts = parsed["facts"] as? [[String: Any]] else {
            throw MemoryExtractionError.badResponse("model response missing a facts list")
        }
        return facts.compactMap { fact in
            guard let text = fact["text"] as? String, !text.isEmpty else { return nil }
            let isJargon = fact["isJargon"] as? Bool ?? false
            return ExtractedFact(text: text, isJargon: isJargon)
        }
    }
}
