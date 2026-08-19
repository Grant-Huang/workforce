import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var apiKey: String = APIKeyStore.load() ?? ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("sk-...", text: $apiKey)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                } header: {
                    Text("OpenAI API Key")
                } footer: {
                    Text("仅保存在本机 Keychain，用于直连 Realtime API。发布上架前请改为后端下发临时令牌，避免把正式密钥打包进 App。")
                }
            }
            .navigationTitle("设置")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        APIKeyStore.save(apiKey)
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
