import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var apiKey: String = APIKeyStore.load() ?? ""
    @State private var baseURL: String = RealtimeConfigStore.baseURL
    @State private var model: String = RealtimeConfigStore.model
    @State private var voice: String = RealtimeConfigStore.voice

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
            }
            .navigationTitle("设置")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        APIKeyStore.save(apiKey)
                        RealtimeConfigStore.baseURL = baseURL
                        RealtimeConfigStore.model = model
                        RealtimeConfigStore.voice = voice
                        dismiss()
                    }
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }
}

#Preview {
    SettingsView()
}
