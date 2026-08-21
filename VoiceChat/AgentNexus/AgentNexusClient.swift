import Foundation

struct AgentNexusMemoryEntry: Codable, Identifiable {
    var id: String { entryId }
    let entryId: String
    let channelId: String
    let layer: String
    let title: String?
    let content: String
    let sortOrder: Int
    let createdBy: String?
    let creatorType: String?
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case entryId = "entry_id"
        case channelId = "channel_id"
        case layer, title, content
        case sortOrder = "sort_order"
        case createdBy = "created_by"
        case creatorType = "creator_type"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

enum AgentNexusClientError: LocalizedError {
    case notConfigured
    case invalidURL
    case httpError(status: Int, body: String)
    case decodingFailed

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "AgentNexus 地址/频道/Token 还没配置完整"
        case .invalidURL: return "AgentNexus 地址格式不对"
        case .httpError(let status, let body): return "AgentNexus 返回错误 \(status)：\(body.prefix(200))"
        case .decodingFailed: return "AgentNexus 返回的数据解析失败"
        }
    }
}

/// Minimal REST client for AgentNexus's channel-scoped memory API.
///
/// No WebSocket, no long-lived connection — just short bearer-authenticated HTTPS
/// calls, per docs/agentnexus-memory-integration-proposal.md. Works today with a
/// short-lived login JWT pasted into Settings, and will keep working unchanged once
/// AgentNexus accepts `pt_...` personal tokens on this same endpoint (proposal §2) —
/// from this client's point of view a bearer token is a bearer token either way.
final class AgentNexusClient {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    /// Fetches all memory entries (ANCHOR/DECISIONS/PROGRESS) for the configured channel.
    /// Used both as the real pull-sync call (once the local cache is built) and, for now,
    /// as a lightweight "test connection" probe from Settings.
    func fetchMemoryEntries() async throws -> [AgentNexusMemoryEntry] {
        guard AgentNexusConfigStore.isConfigured, let token = AgentNexusTokenStore.load() else {
            throw AgentNexusClientError.notConfigured
        }
        let base = AgentNexusConfigStore.baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let channelID = AgentNexusConfigStore.channelID
        guard let url = URL(string: "\(base)/api/v1/channels/\(channelID)/memory/") else {
            throw AgentNexusClientError.invalidURL
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw AgentNexusClientError.decodingFailed
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw AgentNexusClientError.httpError(status: http.statusCode, body: body)
        }
        do {
            return try JSONDecoder().decode([AgentNexusMemoryEntry].self, from: data)
        } catch {
            throw AgentNexusClientError.decodingFailed
        }
    }

    /// Writes a curated memory entry (not raw dialogue) into one of the structured
    /// layers — used when the user explicitly asks to remember something, per
    /// docs/agentnexus-memory-integration-proposal.md's "raw dialogue vs curated
    /// memory" split. Throws on failure so the caller can decide whether/how to tell
    /// the user, unlike `pushMessage` which is fire-and-forget.
    func createMemoryEntry(layer: String, content: String) async throws {
        guard AgentNexusConfigStore.isConfigured, let token = AgentNexusTokenStore.load() else {
            throw AgentNexusClientError.notConfigured
        }
        let base = AgentNexusConfigStore.baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let channelID = AgentNexusConfigStore.channelID
        guard let url = URL(string: "\(base)/api/v1/channels/\(channelID)/memory/") else {
            throw AgentNexusClientError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["layer": layer, "content": content])

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? -1
            let body = String(data: data, encoding: .utf8) ?? ""
            throw AgentNexusClientError.httpError(status: status, body: body)
        }
    }

    /// Fire-and-forget push of a raw conversation turn as a channel message — the
    /// counterpart to `createMemoryEntry` for content that doesn't need curation.
    /// Silently drops if not configured; doesn't block the caller either way.
    func pushMessage(_ text: String, senderType: String = "user") {
        guard AgentNexusConfigStore.isConfigured, let token = AgentNexusTokenStore.load() else { return }
        let base = AgentNexusConfigStore.baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let channelID = AgentNexusConfigStore.channelID
        guard let url = URL(string: "\(base)/api/v1/channels/\(channelID)/messages") else { return }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["content": text, "sender_type": senderType])
        session.dataTask(with: request).resume()
    }
}
