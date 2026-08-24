import SwiftUI
import Foundation

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
                // Transcript is replaced by the tech orb for the whole time the voice
                // session is connected (docs/app-design.md 8.3) -- !isIdle covers
                // connecting/listening/assistantSpeaking but not .error, so an error
                // still surfaces the transcript (with the message in statusLabel below)
                // rather than getting hidden behind the orb.
                if !viewModel.isIdle {
                    voiceOrbView
                } else if viewModel.transcript.isEmpty {
                    emptyStateView
                } else {
                    transcriptList
                }
                statusLabel
                if let dictationError = dictationViewModel.errorMessage, dictationViewModel.state == .idle {
                    Text(dictationError)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
                if let textSessionError = viewModel.textSessionError {
                    Text(textSessionError)
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

    // ChatGPT's accent teal (docs/app-design.md section 1) -- matches web-demo's
    // --user-bubble CSS variable exactly, so the two platforms read as the same product.
    private static let chatGPTGreen = Color(red: 16.0 / 255, green: 163.0 / 255, blue: 127.0 / 255)

    // Light-blue "tech orb" palette, matching web-demo's radial-gradient (docs/app-design.md 8.3).
    private static let orbCore = Color(red: 0.84, green: 0.94, blue: 1.0)
    private static let orbMid = Color(red: 0.42, green: 0.72, blue: 1.0)
    private static let orbEdge = Color(red: 0.18, green: 0.5, blue: 0.88)
    private static let orbGlow = Color(red: 0.31, green: 0.66, blue: 1.0)

    /// Ported from web-demo/static/app.js's updateVoiceOrb(): a sine-driven "breathing"
    /// baseline (so the orb isn't static during CONNECTING or brief silence) blended
    /// with `viewModel.orbLevel` (mic RMS while listening, playback RMS while speaking).
    /// `TimelineView(.animation)` supplies a continuously-advancing time reference for
    /// the breathing term without a separate Timer. The RMS-to-visual-intensity scaling
    /// factor (6x here) is a guess carried over unverified from the web port -- iOS
    /// input/output levels haven't been measured on a real device, so this may need
    /// retuning once someone can actually watch it react to real speech.
    private var voiceOrbView: some View {
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let breathe = 0.05 * (1 + sin(t / 0.9))
            let level = min(1, viewModel.orbLevel * 6)
            let scale = 1 + breathe + level * 0.35
            let glowRadius = 20 + level * 40
            let glowOpacity = 0.35 + level * 0.35

            Circle()
                .fill(
                    RadialGradient(
                        colors: [Self.orbCore, Self.orbMid, Self.orbEdge],
                        center: UnitPoint(x: 0.35, y: 0.32),
                        startRadius: 2,
                        endRadius: 90
                    )
                )
                .frame(width: 140, height: 140)
                .scaleEffect(CGFloat(scale))
                .shadow(color: Self.orbGlow.opacity(glowOpacity), radius: CGFloat(glowRadius))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func bubble(for line: TranscriptLine) -> some View {
        HStack {
            if line.speaker == .assistant { Spacer(minLength: 40) }
            Text(line.text)
                .foregroundStyle(line.speaker == .user ? .white : .primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(line.speaker == .user ? Self.chatGPTGreen : Color(.systemGray6))
                .clipShape(RoundedRectangle(cornerRadius: 16))
            if line.speaker == .user { Spacer(minLength: 40) }
        }
    }

    /// Mirrors web-demo's `.empty` state (docs/app-design.md section 1: "空状态的文案
    /// 往下挪、留白加大") -- guidance text + the same 0-1 time-based suggestion chips,
    /// pushed down with extra top space instead of sitting flush at the top of the
    /// empty chat area. `transcriptList`'s ScrollView greedily fills the VStack's
    /// remaining height even with nothing in it, so this needs the same
    /// maxHeight: .infinity to keep the composer docked at the bottom either way.
    private var emptyStateView: some View {
        VStack(spacing: 20) {
            Text("按下麦克风开始对话，或者直接在下面打字")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            suggestionChips
        }
        .padding(.top, 100)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }

    /// Restrained on-open prompt (see ConversationViewModel.suggestionChips): 0-1
    /// tappable suggestions, no auto-speak/auto-connect. Ported from the web demo.
    private var suggestionChips: some View {
        ForEach(viewModel.suggestionChips) { chip in
            Button(chip.label) {
                viewModel.sendTextSessionMessage(chip.query)
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
                .disabled(!viewModel.isIdle) // typing always goes to the text session, which can't run alongside a live voice conversation
            Button {
                dictationViewModel.errorMessage = nil
                dictationViewModel.start()
            } label: {
                Image(systemName: "mic")
            }
            .disabled(!viewModel.isIdle || !viewModel.isTextSessionIdle) // can't dictate while a live conversation or the text session is connected
            Button(action: sendTypedText) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 28))
            }
            .disabled(!viewModel.isIdle || textDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
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
        viewModel.sendTextSessionMessage(text)
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
        if sendDirectly {
            // finishForDirectSend (unlike finish()) hands back the dictation
            // connection still open when there's text to send, so it can be reused
            // instead of closed-and-reopened — see DictationViewModel.finishForDirectSend
            // and ConversationViewModel.sendTextSessionMessage(_:reusingDictationClient:).
            // Deliberately not also guarding on `!cleaned.isEmpty` here:
            // sendTextSessionMessage(_:reusingDictationClient:) already closes the
            // handed-off connection itself when the text turns out empty, and bailing
            // out before calling it would leak that connection instead.
            guard let (cleaned, client) = await dictationViewModel.finishForDirectSend() else { return }
            viewModel.sendTextSessionMessage(cleaned, reusingDictationClient: client)
        } else {
            guard let cleaned = await dictationViewModel.finish(), !cleaned.isEmpty else { return }
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
        .disabled(dictationViewModel.state != .idle || !viewModel.isTextSessionIdle) // can't run both mics at once, or alongside the text session
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
