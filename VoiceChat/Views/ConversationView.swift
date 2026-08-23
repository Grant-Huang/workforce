import SwiftUI

struct ConversationView: View {
    @StateObject private var viewModel = ConversationViewModel()
    @StateObject private var dictationViewModel = DictationViewModel()
    @State private var showingSettings = false
    @State private var showingMemory = false
    @State private var textDraft = ""
    @FocusState private var textFieldFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                transcriptList
                if viewModel.transcript.isEmpty {
                    suggestionChips
                }
                statusLabel
                if let dictationError = dictationViewModel.errorMessage, dictationViewModel.state == .idle {
                    Text(dictationError)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
                // ChatGPT-style composer (docs/app-design.md section 1): text pill and
                // the live-voice-conversation button sit side by side in one row, not
                // a full-width pill stacked above a separate large mic button.
                if dictationViewModel.state == .idle {
                    composerRow
                } else {
                    dictationRow
                }
            }
            .padding()
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
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

    private var composerRow: some View {
        HStack(spacing: 10) {
            textInputPill
            micButton
        }
    }

    private var textInputPill: some View {
        HStack(spacing: 8) {
            TextField("询问或者说点什么…", text: $textDraft, axis: .vertical)
                .textFieldStyle(.plain)
                .focused($textFieldFocused)
                .onSubmit(sendTypedText)
            Button {
                dictationViewModel.errorMessage = nil
                dictationViewModel.start()
            } label: {
                Image(systemName: "mic")
            }
            .disabled(!viewModel.isIdle) // can't dictate while a live conversation is connected
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
        .frame(maxWidth: .infinity)
    }

    private func sendTypedText() {
        let text = textDraft
        textDraft = ""
        textFieldFocused = false
        viewModel.sendText(text)
    }

    /// Voice-dictate-to-text (docs/app-design.md section 3), replacing textInputRow
    /// while recording/cleaning. X cancels outright; ■ stops and fills the text field
    /// for review before sending; ↑ stops, cleans up, and sends directly — the two
    /// button semantics the user specified explicitly, not two states of one button.
    private var dictationRow: some View {
        HStack(spacing: 10) {
            Button {
                dictationViewModel.cancel()
            } label: {
                Image(systemName: "xmark.circle.fill")
            }
            Text(dictationViewModel.state == .cleaning ? "整理中…" : "正在聆听…")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity)
            Button {
                Task { await finishDictation(sendDirectly: false) }
            } label: {
                Image(systemName: "stop.circle.fill")
            }
            .disabled(dictationViewModel.state != .recording)
            Button {
                Task { await finishDictation(sendDirectly: true) }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
            }
            .disabled(dictationViewModel.state != .recording)
        }
        .font(.system(size: 24))
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(Color(.secondarySystemBackground))
        .clipShape(Capsule())
    }

    private func finishDictation(sendDirectly: Bool) async {
        guard let cleaned = await dictationViewModel.finish(), !cleaned.isEmpty else { return }
        if sendDirectly {
            viewModel.sendText(cleaned)
        } else {
            textDraft = cleaned
            textFieldFocused = true
        }
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
                .font(.system(size: 20))
                .foregroundStyle(.white)
                .frame(width: 46, height: 46)
                .background(micColor)
                .clipShape(Circle())
        }
        .disabled(dictationViewModel.state != .idle) // can't run both mics at once
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
