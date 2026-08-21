import SwiftUI

private enum ConnectionTestState {
    case idle
    case testing
    case success(entryCount: Int)
    case failure(String)
}

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var apiKey: String = APIKeyStore.load() ?? ""
    @State private var baseURL: String = RealtimeConfigStore.baseURL
    @State private var model: String = RealtimeConfigStore.model
    @State private var voice: String = RealtimeConfigStore.voice

    @State private var agentNexusBaseURL: String = AgentNexusConfigStore.baseURL
    @State private var agentNexusChannelID: String = AgentNexusConfigStore.channelID
    @State private var agentNexusToken: String = AgentNexusTokenStore.load() ?? ""
    @State private var agentNexusTestState: ConnectionTestState = .idle

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("API Key", text: $apiKey)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                } header: {
                    Text("API Key")
                } footer: {
                    Text("仅保存在本机 Keychain。发布上架前请改为后端下发临时令牌，避免把正式密钥打包进 App。")
                }

                Section {
                    TextField("wss://...", text: $baseURL)
                        .textContentType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    TextField("模型名称", text: $model)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    TextField("音色", text: $voice)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("连接设置")
                } footer: {
                    Text("默认值指向 Qwen-Audio-Realtime，具体地址/模型名请对照你的 DashScope/百炼控制台核实（这块变化较快）。要改用 OpenAI Realtime API，把地址改成 wss://api.openai.com/v1/realtime、模型改成 gpt-realtime，并到代码里把采集采样率从 16kHz 改回 24kHz。")
                }

                Section {
                    TextField("https://your-agentnexus-server", text: $agentNexusBaseURL)
                        .textContentType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    TextField("个人频道 Channel ID", text: $agentNexusChannelID)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    SecureField("长期 Token（pt_... 或临时登录 JWT）", text: $agentNexusToken)
                        .textContentType(.password)
                        .autocorrectionDisabled()

                    Button {
                        testAgentNexusConnection()
                    } label: {
                        HStack {
                            Text("测试连接")
                            if case .testing = agentNexusTestState {
                                Spacer()
                                ProgressView()
                            }
                        }
                    }
                    .disabled(isAgentNexusTesting)

                    switch agentNexusTestState {
                    case .success(let count):
                        Label("连接成功，读到 \(count) 条记忆", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    case .failure(let message):
                        Label(message, systemImage: "xmark.circle.fill")
                            .foregroundStyle(.red)
                    case .idle, .testing:
                        EmptyView()
                    }
                } header: {
                    Text("智枢（AgentNexus）记忆同步")
                } footer: {
                    Text("对应你在智枢里的个人频道（跟 epicwise 的私聊）。填好三项后点“测试连接”验证能不能读到记忆；token 只存在本机 Keychain。详见 docs/agentnexus-memory-integration-proposal.md。")
                }
            }
            .navigationTitle("设置")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        APIKeyStore.save(apiKey)
                        RealtimeConfigStore.baseURL = baseURL
                        RealtimeConfigStore.model = model
                        RealtimeConfigStore.voice = voice
                        AgentNexusConfigStore.baseURL = agentNexusBaseURL
                        AgentNexusConfigStore.channelID = agentNexusChannelID
                        AgentNexusTokenStore.save(agentNexusToken)
                        dismiss()
                    }
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }

    private var isAgentNexusTesting: Bool {
        if case .testing = agentNexusTestState { return true }
        return false
    }

    private func testAgentNexusConnection() {
        // Save first so the client (which reads from the stores, not local @State) sees
        // what's currently in the form.
        AgentNexusConfigStore.baseURL = agentNexusBaseURL
        AgentNexusConfigStore.channelID = agentNexusChannelID
        AgentNexusTokenStore.save(agentNexusToken)

        agentNexusTestState = .testing
        Task {
            do {
                let entries = try await AgentNexusClient().fetchMemoryEntries()
                agentNexusTestState = .success(entryCount: entries.count)
            } catch {
                agentNexusTestState = .failure(error.localizedDescription)
            }
        }
    }
}

#Preview {
    SettingsView()
}
