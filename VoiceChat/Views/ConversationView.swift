import SwiftUI

struct ConversationView: View {
    @StateObject private var viewModel = ConversationViewModel()
    @State private var showingSettings = false
    @State private var showingMemory = false
    @State private var textDraft = ""
    @FocusState private var textFieldFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                transcriptList
                if viewModel.transcript.isEmpty {
                    suggestionChips
                }
                statusLabel
                micButton
                textInputRow
            }
            .padding()
            .navigationTitle("语音对话")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showingMemory = true
                    } label: {
                        Image(systemName: "brain")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView()
            }
            .sheet(isPresented: $showingMemory) {
                MemoryView(memoryStore: viewModel.memoryStore)
            }
        }
    }

    private var transcriptList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(viewModel.transcript) { line in
                        bubble(for: line).id(line.id)
                    }
                }
            }
            .onChange(of: viewModel.transcript.count) { _, _ in
                if let last = viewModel.transcript.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private func bubble(for line: TranscriptLine) -> some View {
        HStack {
            if line.speaker == .assistant { Spacer(minLength: 40) }
            Text(line.text)
                .padding(10)
                .background(line.speaker == .user ? Color.blue.opacity(0.15) : Color.gray.opacity(0.15))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            if line.speaker == .user { Spacer(minLength: 40) }
        }
    }

    /// Restrained on-open prompt (see ConversationViewModel.suggestionChips): 0-1
    /// tappable suggestions, no auto-speak/auto-connect. Ported from the web demo.
    private var suggestionChips: some View {
        ForEach(viewModel.suggestionChips) { chip in
            Button(chip.label) {
                viewModel.sendText(chip.query)
            }
            .font(.footnote)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(Color(.secondarySystemBackground))
            .clipShape(Capsule())
        }
    }

    private var textInputRow: some View {
        HStack(spacing: 8) {
            TextField("也可以直接打字…", text: $textDraft, axis: .vertical)
                .textFieldStyle(.plain)
                .focused($textFieldFocused)
                .onSubmit(sendTypedText)
            Button(action: sendTypedText) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 28))
            }
            .disabled(textDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color(.secondarySystemBackground))
        .clipShape(Capsule())
    }

    private func sendTypedText() {
        let text = textDraft
        textDraft = ""
        textFieldFocused = false
        viewModel.sendText(text)
    }

    private var statusLabel: some View {
        Text(statusText)
            .font(.footnote)
            .foregroundStyle(.secondary)
    }

    private var statusText: String {
        switch viewModel.state {
        case .idle: return "点击麦克风开始对话"
        case .connecting: return "连接中…"
        case .listening: return "正在聆听…"
        case .assistantSpeaking: return "助手正在说话…（说话即可打断）"
        case .error(let message): return "出错了：\(message)"
        }
    }

    private var micButton: some View {
        Button {
            switch viewModel.state {
            case .idle, .error:
                viewModel.start()
            default:
                viewModel.stop()
            }
        } label: {
            Image(systemName: micIconName)
                .font(.system(size: 40))
                .foregroundStyle(.white)
                .frame(width: 88, height: 88)
                .background(micColor)
                .clipShape(Circle())
        }
        .padding(.bottom, 24)
    }

    private var micIconName: String {
        if case .idle = viewModel.state { return "mic" }
        if case .error = viewModel.state { return "mic" }
        return "stop.fill"
    }

    private var micColor: Color {
        switch viewModel.state {
        case .idle: return .blue
        case .error: return .orange
        case .assistantSpeaking: return .green
        default: return .red
        }
    }
}

#Preview {
    ConversationView()
}
