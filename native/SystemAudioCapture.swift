import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

// stdout is exclusively 16 kHz mono signed little-endian PCM16.
func diagnostic(_ text: String) {
    FileHandle.standardError.write(Data((text + "\n").utf8))
}

enum AudioSource: String {
    case microphone, system, mixed
    var needsMicrophone: Bool { self != .system }
    var needsSystem: Bool { self != .microphone }
}

// Convert capture timestamps to the host clock. One timeline mixes simultaneous audio
// without doubling elapsed time. Missing callbacks represent silence.
final class AudioTimeline {
    static let rate = 16000
    private var samples = [Float](repeating: 0, count: rate * 2)
    private(set) var position = 0

    func append(_ input: [Float], at start: Int) {
        let lower = max(position, start)
        let upper = min(position + samples.count, start + input.count)
        guard lower < upper else { return }
        for index in lower..<upper {
            let value = input[index - start]
            if value.isFinite { samples[index % samples.count] += value }
        }
    }

    func read(_ count: Int) -> [Int16] {
        var pcm = [Int16](repeating: 0, count: count)
        for index in 0..<count {
            let slot = position % samples.count
            let value = max(-1, min(1, samples[slot]))
            pcm[index] = Int16(max(-32768, min(32767, Int(value * 32767)))).littleEndian
            samples[slot] = 0
            position += 1
        }
        return pcm
    }
}

func hostSeconds() -> Double {
    CMClockGetTime(CMClockGetHostTimeClock()).seconds
}

final class AudioMixer: @unchecked Sendable {
    let queue = DispatchQueue(label: "parrot.audio.mix")
    private let timeline = AudioTimeline()
    private var origin: Double?
    private var timer: DispatchSourceTimer?

    func start() {
        queue.sync {
            origin = hostSeconds()
            let timer = DispatchSource.makeTimerSource(queue: queue)
            // Allow 200 ms for either capture API to deliver its samples.
            timer.schedule(deadline: .now() + 0.22, repeating: 0.02)
            timer.setEventHandler { [weak self] in self?.flush(until: hostSeconds() - 0.2) }
            self.timer = timer
            timer.resume()
        }
    }

    func append(_ samples: [Float], timestamp: Double) {
        queue.async { [self] in
            guard let origin = origin, timestamp.isFinite else { return }
            let offset = (timestamp - origin) * Double(AudioTimeline.rate)
            // Reject unrelated clock values before converting to Int.
            guard offset > Double(timeline.position - AudioTimeline.rate * 2),
                  offset < Double(timeline.position + AudioTimeline.rate * 2) else { return }
            timeline.append(samples, at: Int(offset.rounded()))
        }
    }

    private func flush(until time: Double) {
        guard let origin = origin else { return }
        let end = max(0, Int((time - origin) * Double(AudioTimeline.rate)))
        while timeline.position < end {
            let pcm = timeline.read(min(320, end - timeline.position))
            pcm.withUnsafeBytes { FileHandle.standardOutput.write(Data($0)) }
        }
    }

    // SIGTERM is delivered on queue after already queued audio callbacks.
    func finish() {
        timer?.cancel()
        flush(until: hostSeconds())
    }
}

final class AudioOutput: NSObject, SCStreamOutput, SCStreamDelegate,
                         AVCaptureAudioDataOutputSampleBufferDelegate, @unchecked Sendable {
    let mixer: AudioMixer
    weak var microphoneSession: AVCaptureSession?
    init(mixer: AudioMixer) { self.mixer = mixer }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        diagnostic("本机音频采集中断：\(error.localizedDescription)")
        exit(2)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        if outputType == .audio { receive(sampleBuffer, timestamp: sampleBuffer.presentationTimeStamp.seconds) }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let clock = microphoneSession?.synchronizationClock else { return }
        let timestamp = CMSyncConvertTime(sampleBuffer.presentationTimeStamp,
                                          from: clock, to: CMClockGetHostTimeClock())
        receive(sampleBuffer, timestamp: timestamp.seconds)
    }

    private func receive(_ sampleBuffer: CMSampleBuffer, timestamp: Double) {
        guard sampleBuffer.isValid,
              let description = sampleBuffer.formatDescription,
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description),
              let format = AVAudioFormat(streamDescription: asbd),
              let source = AVAudioPCMBuffer(pcmFormat: format,
                  frameCapacity: AVAudioFrameCount(sampleBuffer.numSamples)) else { return }
        source.frameLength = source.frameCapacity
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer, at: 0, frameCount: Int32(sampleBuffer.numSamples),
            into: source.mutableAudioBufferList)
        guard status == noErr else {
            diagnostic("无法读取音频采样：\(status)")
            exit(3)
        }
        guard format.sampleRate == 16000, format.channelCount == 1,
              let floats = source.floatChannelData?[0] else {
            diagnostic("设备返回不支持的音频格式：\(format)")
            exit(4)
        }
        mixer.append(Array(UnsafeBufferPointer(start: floats, count: Int(source.frameLength))),
                     timestamp: timestamp)
    }
}

@main
struct ParrotCapture {
    static func main() async {
        if CommandLine.arguments.contains("--self-test") { selfTest(); return }
        let arguments = Array(CommandLine.arguments.dropFirst())
        let source: AudioSource
        if arguments.isEmpty { source = .system }
        else if arguments.count == 2, arguments[0] == "--source",
                let parsed = AudioSource(rawValue: arguments[1]) { source = parsed }
        else { diagnostic("用法：ParrotCapture --source microphone|system|mixed"); exit(1) }
        guard #available(macOS 13.0, *) else {
            diagnostic("音频采集需要 macOS 13 或更新版本。")
            exit(1)
        }
        do {
            let mixer = AudioMixer()
            let output = AudioOutput(mixer: mixer)
            var microphone: AVCaptureSession?
            var microphoneError: NSObjectProtocol?
            if source.needsMicrophone {
                guard await AVCaptureDevice.requestAccess(for: .audio) else {
                    diagnostic("麦克风权限未生效。请在「系统设置 → 隐私与安全性 → 麦克风」中允许 Parrot，再重试。")
                    exit(1)
                }
                guard let device = AVCaptureDevice.default(for: .audio) else {
                    diagnostic("未找到可用的麦克风，请连接或启用麦克风后重试。")
                    exit(1)
                }
                let session = AVCaptureSession()
                let input = try AVCaptureDeviceInput(device: device)
                let audio = AVCaptureAudioDataOutput()
                audio.audioSettings = [
                    AVFormatIDKey: kAudioFormatLinearPCM,
                    AVSampleRateKey: 16000,
                    AVNumberOfChannelsKey: 1,
                    AVLinearPCMBitDepthKey: 32,
                    AVLinearPCMIsFloatKey: true,
                    AVLinearPCMIsNonInterleaved: false,
                ]
                audio.setSampleBufferDelegate(output, queue: DispatchQueue(label: "parrot.audio.microphone"))
                guard session.canAddInput(input), session.canAddOutput(audio) else {
                    diagnostic("无法启动麦克风，请检查输入设备后重试。")
                    exit(1)
                }
                session.addInput(input)
                session.addOutput(audio)
                output.microphoneSession = session
                microphoneError = NotificationCenter.default.addObserver(
                    forName: .AVCaptureSessionRuntimeError, object: session, queue: nil
                ) { notification in
                    let error = notification.userInfo?[AVCaptureSessionErrorKey] as? NSError
                    diagnostic("麦克风采集中断：\(error?.localizedDescription ?? "请检查输入设备后重试。")")
                    exit(2)
                }
                session.startRunning()
                guard session.isRunning else {
                    diagnostic("麦克风启动失败，请检查麦克风权限与输入设备。")
                    exit(1)
                }
                microphone = session
            }
            var stream: SCStream?
            if source.needsSystem {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                guard let display = content.displays.first else {
                    diagnostic("未找到显示器，无法启动本机音频采集。")
                    exit(1)
                }
                let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
                let config = SCStreamConfiguration()
                config.capturesAudio = true
                config.excludesCurrentProcessAudio = false
                config.sampleRate = 16000
                config.channelCount = 1
                // No video output is registered or saved.
                config.width = 2
                config.height = 2
                config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
                config.queueDepth = 3
                let capture = SCStream(filter: filter, configuration: config, delegate: output)
                try capture.addStreamOutput(output, type: .audio,
                                            sampleHandlerQueue: DispatchQueue(label: "parrot.audio.system"))
                try await capture.startCapture()
                stream = capture
            }
            // Stop/Pause must flush the delayed tail before closing the pipe.
            signal(SIGTERM, SIG_IGN)
            let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: mixer.queue)
            termination.setEventHandler { mixer.finish(); exit(0) }
            termination.resume()
            mixer.start()
            diagnostic("READY")
            while true {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                withExtendedLifetime((stream, microphone, output, microphoneError, termination)) {}
            }
        } catch {
            let failure = error as NSError
            if failure.domain == SCStreamErrorDomain && failure.code == SCStreamError.Code.userDeclined.rawValue {
                diagnostic("系统声音权限未生效。请在「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」中允许 Parrot，并从菜单栏退出后重新打开。若开关已开启，请删除旧的 Parrot 条目，再添加当前 Parrot.app 并授权。")
            } else {
                diagnostic("无法采集音频：\(error.localizedDescription) [\(failure.domain):\(failure.code)]")
            }
            exit(1)
        }
    }

    // Synthetic audio only; no permission requests or real devices.
    static func selfTest() {
        let timeline = AudioTimeline()
        timeline.append([0.25, 0.25, 0.75, -0.75], at: 0)
        timeline.append([0.25, 0.25, 0.75, -0.75], at: 0)
        let mixed = timeline.read(4).map { Int16(littleEndian: $0) }
        precondition(mixed == [16383, 16383, 32767, -32767], "mixed signal or clipping failed")
        precondition(timeline.position == 4, "mixing must not double duration")
        timeline.append([0.5, 0.5], at: 6)
        precondition(timeline.read(4) == [0, 0, 16383, 16383], "silence must preserve timestamps")
        timeline.append([.nan, .infinity, -.infinity], at: 8)
        precondition(timeline.read(3) == [0, 0, 0], "invalid samples must be silent")
        timeline.append([1, 1], at: 0)
        precondition(timeline.read(2) == [0, 0], "late callbacks must not replay old audio")
        _ = timeline.read(AudioTimeline.rate * 2)
        precondition(timeline.read(4) == [0, 0, 0, 0], "ring buffer must clear old samples")
        diagnostic("CAPTURE_SELF_TEST_PASSED: mixed audio, clipping, shared timeline, silence, late callbacks")
    }
}
