import Foundation
import Network
import NIOCore
import NIOHTTP1
import NIOWebSocket
import NIOTransportServices

/// WebSocket client for Realtime-API-style voice endpoints (OpenAI Realtime API,
/// Qwen-Omni-Realtime / Qwen-Audio-Realtime — see `RealtimeModels.swift`).
///
/// Owns the socket connection and translates between raw JSON events and the
/// typed callbacks the view model consumes. Networking-only: it doesn't know
/// anything about audio capture/playback (see `AudioIOManager`).
///
/// Built on `swift-nio-transport-services` instead of `URLSessionWebSocketTask` or
/// Starscream — see docs/realtime-websocket-transport-design.md for the full writeup.
/// Short version: `URLSessionWebSocketTask` was tracked (2026-08-24, real-device
/// debugging) to a 100%-reproducible connection failure caused by it negotiating
/// HTTP/2 ALPN against servers that advertise it (this one does). Starscream, adopted
/// to work around that, turned out to have its own matching unresolved bugs (a missing
/// Authorization header on the handshake request, and a WebSocket frame-parsing bug —
/// both with open upstream issues). `NIOTSConnectionBootstrap.tlsOptions(_:)` gives
/// direct control over the TLS ALPN protocol list via `Network.framework`, so instead
/// of hoping a third-party library's internal transport choice happens to avoid h2,
/// this explicitly restricts ALPN to `http/1.1` — the actual root cause, addressed
/// directly rather than worked around.
///
/// Requires iOS 17+ (`NIOAsyncChannel`/`NIOTypedWebSocketClientUpgrader`'s async APIs) --
/// already the project's deployment target (project.yml), so no `@available` needed here.
final class RealtimeClient: NSObject {
    private typealias WSChannel = NIOAsyncChannel<WebSocketFrame, WebSocketFrame>

    /// One event loop is plenty for a single WebSocket connection; a fresh group per
    /// instance (rather than a shared singleton) keeps lifecycle simple -- shut down in
    /// `disconnect()`/`deinit` so its background thread doesn't leak.
    private let eventLoopGroup = NIOTSEventLoopGroup(loopCount: 1)
    private var outbound: NIOAsyncChannelOutboundWriter<WebSocketFrame>?
    private var receiveTask: Task<Void, Never>?
    /// `URLSessionWebSocketTask` sends pings under the hood automatically; NIO's raw
    /// WebSocket channel does not, so the peer (and any proxy/load balancer in front of
    /// it) can silently time out an otherwise-healthy connection. Sent every 20s while
    /// connected, cancelled on disconnect.
    private var pingTask: Task<Void, Never>?
    /// Frame reassembly for fragmented messages (`fin: false` followed by `.continuation`
    /// frames) -- `URLSessionWebSocketTask` and Starscream both did this transparently,
    /// so nothing above this layer has ever had to think about it; NIO hands over raw
    /// wire frames and expects the caller to reassemble.
    private var fragmentBuffer: Data?

    var onAudioDelta: ((Data) -> Void)?
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptDone: ((String) -> Void)?
    /// Text-session counterparts of `onTranscriptDelta`/`onTranscriptDone` — see
    /// `RealtimeIncomingEvent.textDelta`/`.textDone`.
    var onTextDelta: ((String) -> Void)?
    var onTextDone: ((String) -> Void)?
    var onUserTranscript: ((String) -> Void)?
    var onSpeechStarted: (() -> Void)?
    var onResponseDone: (() -> Void)?
    var onError: ((String) -> Void)?
    var onDisconnect: ((Error?) -> Void)?

    /// Resolved on the next `session.updated` event — see `updateInstructions`.
    private var pendingInstructionsAck: (() -> Void)?

    /// - Parameters:
    ///   - baseURL: The realtime endpoint, without the `model` query param, e.g.
    ///     `wss://api.openai.com/v1/realtime` or
    ///     `wss://<workspace-id>.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime`.
    ///   - model: Model id sent as the `model` query param (e.g. `gpt-realtime`,
    ///     `qwen-audio-3.0-realtime-plus`). Check your provider's console for the
    ///     current name — these change more often than the wire protocol does.
    /// `autoRespond: false` (the default here) leaves the server detecting end-of-speech
    /// and committing the input buffer, but lets the client decide *when* to actually
    /// trigger a reply via `requestResponse()` — see `RealtimeOutgoingEvent.sessionUpdate`.
    ///
    /// `onSessionReady` fires once the initial `session.update` is acked via
    /// `session.updated` — callers must wait for it before treating the session as
    /// actually usable (e.g. before showing "connected" or sending a turn). Firing the
    /// initial config and moving on without waiting for this was a real bug: nothing
    /// verified the session had actually come up, so a socket that opened but never
    /// got a working session (a real observed Qwen failure mode, see
    /// docs/qwen-realtime-voice-setup.md) looked identical to a healthy connection.
    func connect(baseURL: String, apiKey: String, model: String, instructions: String, voice: String, turnDetection: String? = "server_vad", autoRespond: Bool = false, modalities: [String] = ["audio", "text"], onSessionReady: @escaping () -> Void) {
        guard var components = URLComponents(string: baseURL) else {
            onError?("invalid WebSocket URL: \(baseURL)")
            return
        }
        components.queryItems = [URLQueryItem(name: "model", value: model)]
        guard let url = components.url, let host = url.host else {
            onError?("could not build WebSocket URL from: \(baseURL)")
            return
        }
        let port = url.port ?? 443
        let uri = (components.percentEncodedPath.isEmpty ? "/" : components.percentEncodedPath) + (components.percentEncodedQuery.map { "?\($0)" } ?? "")
        let isOpenAI = host.contains("openai.com")

        pendingInstructionsAck = onSessionReady
        fragmentBuffer = nil // this instance may be reused across connect()/disconnect() cycles
        let initialSessionUpdate = RealtimeOutgoingEvent.sessionUpdate(instructions: instructions, voice: voice, turnDetection: turnDetection, autoRespond: autoRespond, modalities: modalities)

        receiveTask = Task { [weak self, eventLoopGroup] in
            guard let self else { return }
            do {
                let channel = try await Self.openWebSocket(group: eventLoopGroup, host: host, port: port, uri: uri, apiKey: apiKey, isOpenAI: isOpenAI)
                try await channel.executeThenClose { inbound, outbound in
                    await MainActor.run {
                        self.outbound = outbound
                        self.startPingTask()
                    }
                    try await Self.write(initialSessionUpdate, to: outbound)
                    for try await frame in inbound {
                        if Task.isCancelled { return }
                        await self.handleFrame(frame)
                        if frame.opcode == .connectionClose { return }
                    }
                }
                await MainActor.run { self.onDisconnect?(nil) }
            } catch is CancellationError {
                // disconnect() cancelled receiveTask -- expected, not an error to surface.
            } catch {
                await MainActor.run { self.onError?("connect failed: \(error)") }
            }
            await MainActor.run {
                self.outbound = nil
                self.stopPingTask()
            }
        }
    }

    /// Builds the connection and performs the WebSocket upgrade. A free function (no
    /// `self` capture) because `NIOTSConnectionBootstrap.connect`'s `channelInitializer`
    /// closure is `@Sendable` and this class isn't `Sendable` -- everything it needs is
    /// passed in as plain, already-`Sendable` values instead.
    private static func openWebSocket(group: NIOTSEventLoopGroup, host: String, port: Int, uri: String, apiKey: String, isOpenAI: Bool) async throws -> WSChannel {
        enum UpgradeResult {
            case websocket(WSChannel)
            case notUpgraded
        }

        // Real-device/simulator testing (2026-08-24) found connect() hanging silently
        // until ConversationViewModel's own 8s "连接超时，请重试" UI-level timeout fired,
        // with no error detail -- there was no NIO-level connect timeout, so a connection
        // attempt that can't complete (DNS, TCP handshake, TLS) just sat there instead of
        // failing with a real error. 5s (shorter than the 8s UI timeout) so a genuine
        // failure surfaces an actionable NIO error via onError before the generic
        // fallback message fires.
        let bootstrap = NIOTSConnectionBootstrap(group: group)
            .tlsOptions(http1OnlyTLSOptions())
            .connectTimeout(.seconds(5))

        // NOTE: unlike the ClientBootstrap-based reference example (Sources/
        // NIOWebSocketClient in apple/swift-nio), NIOTSConnectionBootstrap has no sync
        // `connect(host:port:channelInitializer:)` overload -- only the async one used
        // below, which returns `Output` directly (already unwrapped once), not
        // `EventLoopFuture<Output>`. So `channelInitializer` here returns
        // `EventLoopFuture<UpgradeResult>` directly (single-wrapped) rather than nesting
        // it inside another `channel.eventLoop.makeCompletedFuture { }` the way the
        // ClientBootstrap example does -- that nested form doesn't type-check against
        // this async API (caught by a real build failure, not guessed).
        let upgradeResult: UpgradeResult = try await bootstrap.connect(host: host, port: port) { channel in
            do {
                let upgrader = NIOTypedWebSocketClientUpgrader<UpgradeResult>(
                    upgradePipelineHandler: { channel, _ in
                        channel.eventLoop.makeCompletedFuture {
                            .websocket(try WSChannel(wrappingChannelSynchronously: channel))
                        }
                    }
                )

                var headers = HTTPHeaders()
                headers.add(name: "Host", value: host)
                headers.add(name: "Authorization", value: "Bearer \(apiKey)")
                if isOpenAI {
                    headers.add(name: "OpenAI-Beta", value: "realtime=v1")
                }

                let requestHead = HTTPRequestHead(version: .http1_1, method: .GET, uri: uri, headers: headers)
                let upgradeConfiguration = NIOTypedHTTPClientUpgradeConfiguration(
                    upgradeRequestHead: requestHead,
                    upgraders: [upgrader],
                    notUpgradingCompletionHandler: { channel in
                        channel.eventLoop.makeCompletedFuture { .notUpgraded }
                    }
                )

                return try channel.pipeline.syncOperations.configureUpgradableHTTPClientPipeline(
                    configuration: .init(upgradeConfiguration: upgradeConfiguration)
                )
            } catch {
                return channel.eventLoop.makeFailedFuture(error)
            }
        }

        switch upgradeResult {
        case .websocket(let channel):
            return channel
        case .notUpgraded:
            // NOTE: the typed upgrader's `notUpgradingCompletionHandler` doesn't hand back
            // the rejected HTTPResponseHead, so unlike the URLSessionWebSocketTask/
            // Starscream-era code this can't surface a `[handshake HTTP xxx]` diagnostic
            // -- a real regression versus #12/#15's diagnostic, called out in the design
            // doc and left as a follow-up rather than guessed at against unverified NIO
            // pipeline internals. `curl --http1.1` against the same URL remains the
            // fallback to see the actual rejected status/body (used throughout this
            // debugging session already).
            throw NSError(domain: "RealtimeClient.WebSocket", code: -1, userInfo: [NSLocalizedDescriptionKey: "HTTP upgrade rejected"])
        }
    }

    /// Only offers `http/1.1` in ALPN -- see the class doc comment for why this specific
    /// line is the actual fix this whole rewrite exists for.
    ///
    /// UNVERIFIED (no Swift toolchain in this environment): `sec_protocol_options_add_tls_
    /// application_protocol`'s exact signature couldn't be confirmed against Apple's docs
    /// (JS-rendered, not fetchable here) or by compiling. Matches the well-established
    /// public pattern for setting ALPN via `NWProtocolTLS.Options.securityProtocolOptions`
    /// from memory/community examples, but double-check this compiles before relying on it.
    private static func http1OnlyTLSOptions() -> NWProtocolTLS.Options {
        let options = NWProtocolTLS.Options()
        sec_protocol_options_add_tls_application_protocol(options.securityProtocolOptions, "http/1.1")
        return options
    }

    private static func write(_ payload: [String: Any], to outbound: NIOAsyncChannelOutboundWriter<WebSocketFrame>) async throws {
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        var buffer = ByteBufferAllocator().buffer(capacity: data.count)
        buffer.writeBytes(data)
        try await outbound.write(WebSocketFrame(fin: true, opcode: .binary, maskKey: .random(), data: buffer))
    }

    /// Every 20s while connected -- see `pingTask`'s doc comment for why this exists.
    private func startPingTask() {
        pingTask?.cancel()
        pingTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                guard !Task.isCancelled, let self, let outbound = self.outbound else { return }
                let buffer = ByteBufferAllocator().buffer(capacity: 0)
                try? await outbound.write(WebSocketFrame(fin: true, opcode: .ping, maskKey: .random(), data: buffer))
            }
        }
    }

    private func stopPingTask() {
        pingTask?.cancel()
        pingTask = nil
    }

    /// Submits a typed or dictated-and-cleaned-up text message as a user turn.
    func sendUserText(_ text: String) {
        send(RealtimeOutgoingEvent.conversationItemCreateUserText(text))
    }

    func disconnect() {
        // eventLoopGroup is deliberately NOT shut down here (only in deinit) -- callers
        // (ConversationViewModel, DictationViewModel) hold one RealtimeClient instance
        // across repeated connect()/disconnect() cycles rather than making a fresh one
        // each time, and a shut-down NIOTSEventLoopGroup can't be reused for a later
        // connect(). One idle event loop (loopCount: 1) between connections is cheap.
        receiveTask?.cancel()
        receiveTask = nil
        stopPingTask()
        outbound = nil
    }

    func sendAudioChunk(_ data: Data) {
        send(RealtimeOutgoingEvent.inputAudioAppend(base64Audio: data.base64EncodedString()))
    }

    /// Patches the session's system instructions (e.g. base prompt + retrieved local
    /// memory) and calls `completion` once the server confirms it via `session.updated`.
    /// Callers must wait for that ack before calling `requestResponse()` — sending a
    /// second `session.update` before the first is acked raced and produced an empty
    /// reply in testing (see `RealtimeOutgoingEvent.sessionInstructionsPatch`).
    func updateInstructions(_ instructions: String, completion: @escaping () -> Void) {
        pendingInstructionsAck = completion
        send(RealtimeOutgoingEvent.sessionInstructionsPatch(instructions: instructions))
    }

    /// Triggers response generation — required when the session was configured with
    /// `autoRespond: false`.
    func requestResponse() {
        send(RealtimeOutgoingEvent.responseCreate())
    }

    func cancelResponse() {
        send(RealtimeOutgoingEvent.responseCancel())
    }

    private func send(_ payload: [String: Any]) {
        guard let outbound else { return }
        Task { [weak self] in
            do {
                try await Self.write(payload, to: outbound)
            } catch {
                await MainActor.run { self?.onError?("send failed: \(error)") }
            }
        }
    }

    /// Reassembles fragmented messages (see `fragmentBuffer`) and dispatches control
    /// frames. `.unmaskedData` (not `.data`) is used deliberately: server-to-client
    /// frames are never supposed to be masked per RFC 6455, but Starscream's "masked and
    /// rsv data is not currently supported" bug (the reason this class no longer uses
    /// Starscream — see docs/realtime-websocket-transport-design.md) suggests something
    /// in this path (a misbehaving proxy, most likely) sometimes does mask them anyway.
    /// `.unmaskedData` handles both cases instead of erroring on the protocol violation.
    @MainActor
    private func handleFrame(_ frame: WebSocketFrame) {
        switch frame.opcode {
        case .text, .binary:
            let chunk = Data(frame.unmaskedData.readableBytesView)
            if frame.fin {
                handle(chunk)
            } else {
                fragmentBuffer = chunk
            }
        case .continuation:
            let chunk = Data(frame.unmaskedData.readableBytesView)
            fragmentBuffer = (fragmentBuffer ?? Data()) + chunk
            if frame.fin {
                let complete = fragmentBuffer ?? Data()
                fragmentBuffer = nil
                handle(complete)
            }
        case .connectionClose:
            // No onDisconnect?(nil) here -- the caller's receive loop (connect()) sees
            // this same opcode, returns from executeThenClose's closure, and that's what
            // actually fires onDisconnect once the channel finishes closing. Calling it
            // here too would fire it twice for a server-initiated close.
            break
        case .ping, .pong:
            break
        default:
            break
        }
    }

    private func handle(_ data: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

        switch RealtimeIncomingEvent(json: json) {
        case .audioDelta(let base64):
            if let audio = Data(base64Encoded: base64) {
                onAudioDelta?(audio)
            }
        case .transcriptDelta(let text):
            onTranscriptDelta?(text)
        case .transcriptDone(let text):
            onTranscriptDone?(text)
        case .textDelta(let text):
            onTextDelta?(text)
        case .textDone(let text):
            onTextDone?(text)
        case .userTranscript(let text):
            onUserTranscript?(text)
        case .speechStarted:
            onSpeechStarted?()
        case .sessionUpdated:
            pendingInstructionsAck?()
            pendingInstructionsAck = nil
        case .responseDone:
            onResponseDone?()
        case .error(let message):
            onError?(message)
        case .unhandled:
            break
        }
    }

    deinit {
        receiveTask?.cancel()
        pingTask?.cancel()
        eventLoopGroup.shutdownGracefully { _ in }
    }
}
