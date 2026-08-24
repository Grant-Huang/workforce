import AVFoundation

/// Captures microphone audio and plays back assistant audio.
///
/// The Realtime API this app talks to (Qwen-Omni-Realtime / Qwen-Audio-Realtime)
/// uses an *asymmetric* wire format: 16kHz mono PCM16 for audio sent up to the
/// server, 24kHz mono PCM16 for audio streamed back. (OpenAI's Realtime API uses
/// 24kHz both ways — if you switch providers, update `inputWireFormat` to match.)
///
/// AVAudioEngine's hardware format is whatever the device gives us (usually
/// 48kHz float32); `AVAudioConverter` bridges that to/from the wire format so
/// neither the network layer nor the UI has to think about sample rates.
final class AudioIOManager {
    private let engine = AVAudioEngine()
    private let playerNode = AVAudioPlayerNode()

    private var inputConverter: AVAudioConverter?
    private let inputWireFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 16000,
        channels: 1,
        interleaved: true
    )!
    private let outputWireFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 24000,
        channels: 1,
        interleaved: true
    )!

    var onCapturedChunk: ((Data) -> Void)?
    /// RMS amplitude (roughly 0...0.3 for normal speech, unclamped) of the mic input /
    /// assistant playback, sampled once per tap callback -- drives the voice-session
    /// "tech orb" (see ConversationView.voiceOrbView). Fired on an audio thread, same
    /// as `onCapturedChunk`; callers must hop back to the main actor before touching
    /// UI-facing state.
    var onInputLevel: ((Float) -> Void)?
    var onOutputLevel: ((Float) -> Void)?

    func start() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetooth])
        try session.setActive(true)

        let input = engine.inputNode
        let hardwareFormat = input.outputFormat(forBus: 0)
        inputConverter = AVAudioConverter(from: hardwareFormat, to: inputWireFormat)

        engine.attach(playerNode)
        engine.connect(playerNode, to: engine.mainMixerNode, format: outputWireFormat)

        input.installTap(onBus: 0, bufferSize: 2048, format: hardwareFormat) { [weak self] buffer, _ in
            self?.convertAndEmit(buffer)
            self?.emitLevel(buffer, to: self?.onInputLevel)
        }
        // Separate tap on the mixer output (not the player node directly) so this picks
        // up whatever's actually audible, same signal the speaker gets.
        engine.mainMixerNode.installTap(onBus: 0, bufferSize: 1024, format: nil) { [weak self] buffer, _ in
            self?.emitLevel(buffer, to: self?.onOutputLevel)
        }

        engine.prepare()
        try engine.start()
        playerNode.play()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.mainMixerNode.removeTap(onBus: 0)
        playerNode.stop()
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    /// Schedules a chunk of PCM16/24kHz assistant audio for playback.
    func play(pcm16 data: Data) {
        let frameCount = UInt32(data.count / 2)
        guard frameCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: outputWireFormat, frameCapacity: frameCount) else { return }
        buffer.frameLength = frameCount

        data.withUnsafeBytes { raw in
            guard let src = raw.bindMemory(to: Int16.self).baseAddress,
                  let dst = buffer.int16ChannelData?[0] else { return }
            dst.update(from: src, count: Int(frameCount))
        }

        playerNode.scheduleBuffer(buffer, completionHandler: nil)
    }

    /// Drops any queued/playing assistant audio — called when the user barges in.
    func interruptPlayback() {
        playerNode.stop()
        playerNode.play()
    }

    private func emitLevel(_ buffer: AVAudioPCMBuffer, to callback: ((Float) -> Void)?) {
        guard let callback, let data = buffer.floatChannelData?[0] else { return }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else { return }
        var sum: Float = 0
        for i in 0..<frameLength { sum += data[i] * data[i] }
        callback(sqrt(sum / Float(frameLength)))
    }

    private func convertAndEmit(_ buffer: AVAudioPCMBuffer) {
        guard let converter = inputConverter else { return }
        let ratio = inputWireFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: inputWireFormat, frameCapacity: capacity) else { return }

        var error: NSError?
        var consumed = false
        converter.convert(to: outBuffer, error: &error) { _, outStatus in
            if consumed {
                outStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            outStatus.pointee = .haveData
            return buffer
        }

        guard error == nil, outBuffer.frameLength > 0,
              let channelData = outBuffer.int16ChannelData else { return }
        let byteCount = Int(outBuffer.frameLength) * 2
        let data = Data(bytes: channelData[0], count: byteCount)
        onCapturedChunk?(data)
    }
}
