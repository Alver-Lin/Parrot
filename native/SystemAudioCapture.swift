import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

// stdout is exclusively 16 kHz mono signed little-endian PCM16.
// ScreenCaptureKit permission belongs to the terminal/app launching this helper.
func diagnostic(_ text: String) {
    FileHandle.standardError.write(Data((text + "\n").utf8))
}

final class AudioOutput: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        diagnostic("系统音频采集中断：\(error.localizedDescription)")
        exit(2)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .audio, sampleBuffer.isValid,
              let description = sampleBuffer.formatDescription,
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description),
              let format = AVAudioFormat(streamDescription: asbd) else { return }
        guard let source = AVAudioPCMBuffer(pcmFormat: format,
                  frameCapacity: AVAudioFrameCount(sampleBuffer.numSamples)) else { return }
        source.frameLength = source.frameCapacity
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer, at: 0, frameCount: Int32(sampleBuffer.numSamples),
            into: source.mutableAudioBufferList)
        guard status == noErr else {
            diagnostic("无法读取音频采样：\(status)")
            exit(3)
        }
        // ScreenCaptureKit is configured to emit 16 kHz mono Float32. Validate
        // the actual format rather than interpreting an unexpected format.
        guard format.sampleRate == 16000, format.channelCount == 1,
              let floats = source.floatChannelData?[0] else {
            diagnostic("系统返回不支持的音频格式：\(format)")
            exit(4)
        }
        var pcm = [Int16](repeating: 0, count: Int(source.frameLength))
        for i in 0..<pcm.count {
            let value = floats[i].isFinite ? max(-1, min(1, floats[i])) : 0
            pcm[i] = Int16(max(-32768, min(32767, Int(value * 32767)))).littleEndian
        }
        pcm.withUnsafeBytes { buffer in
            FileHandle.standardOutput.write(Data(buffer))
        }
    }
}

@main
struct ParrotCapture {
    static func main() async {
        guard #available(macOS 13.0, *) else {
            diagnostic("系统音频采集需要 macOS 13 或更新版本。")
            exit(1)
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first else {
                diagnostic("未找到显示器，无法启动系统音频采集。")
                exit(1)
            }
            let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            let config = SCStreamConfiguration()
            config.capturesAudio = true
            config.excludesCurrentProcessAudio = false
            config.sampleRate = 16000
            config.channelCount = 1
            // ScreenCaptureKit requires a display filter; no video output is
            // registered or saved. Reduce the unused video stream's footprint.
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            config.queueDepth = 3
            let output = AudioOutput()
            let stream = SCStream(filter: filter, configuration: config, delegate: output)
            try stream.addStreamOutput(output, type: .audio,
                                       sampleHandlerQueue: DispatchQueue(label: "parrot.audio"))
            try await stream.startCapture()
            diagnostic("READY")
            // The parent sends SIGTERM to stop capture. Exit closes the pipe;
            // Python drains the pipe and transcribes the final partial audio chunk.
            while true {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                _ = stream
            }
        } catch {
            let failure = error as NSError
            if failure.domain == SCStreamErrorDomain && failure.code == SCStreamError.Code.userDeclined.rawValue {
                diagnostic("系统声音权限未生效。请在「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」中允许 Parrot，并从菜单栏退出后重新打开。若开关已开启，请删除旧的 Parrot 条目，再添加当前 Parrot.app 并授权。")
            } else {
                diagnostic("无法采集系统音频：\(error.localizedDescription) [\(failure.domain):\(failure.code)]")
            }
            exit(1)
        }
    }
}
