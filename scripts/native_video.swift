// Read videos with macOS AVFoundation, printing one JSON line of fingerprints
// per file: the tracks as QuickTime Player sees them, with their size,
// rotation, timing, color, HDR and codec configuration, and frames as it
// shows them. scripts/regression.py compiles this helper and compares
// fingerprints: equal ones mean macOS plays the same video.
import AVFoundation
import CoreGraphics
import CryptoKit
import Foundation

let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
// Format description extensions that hold what MagicDispel clears, a sample
// entry's compressor name and vendor, or all of its bytes.
let clearedExtensions = [
    kCMFormatDescriptionExtension_FormatName, kCMFormatDescriptionExtension_Vendor,
    kCMFormatDescriptionExtension_VerbatimSampleDescription, kCMFormatDescriptionExtension_VerbatimISOSampleEntry,
].map { $0 as String }

func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// A property list value as JSON can hold it, with binary data hashed.
func describe(_ value: Any) -> Any {
    switch value {
    case let data as Data: return "sha256:" + sha256(data)
    case let dictionary as [String: Any]: return dictionary.mapValues(describe)
    case let array as [Any]: return array.map(describe)
    case let number as NSNumber: return number
    case let text as String: return text
    default: return String(describing: value)
    }
}

func fourCC(_ code: FourCharCode) -> String {
    String(bytes: [24, 16, 8, 0].map { UInt8((code >> $0) & 0xFF) }, encoding: .macOSRoman) ?? String(code)
}

func time(_ value: CMTime) -> [Int64] { [value.value, Int64(value.timescale)] }

/// What a frame looks like in sRGB.
func renderDigest(_ image: CGImage) -> String? {
    let info = CGImageAlphaInfo.premultipliedLast.rawValue
    guard let context = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                                  bytesPerRow: image.width * 4, space: srgb, bitmapInfo: info),
          let base = context.data else { return nil }
    context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    return sha256(Data(bytes: base, count: image.width * 4 * image.height))
}

func fingerprint(_ path: String) async -> [String: Any] {
    var result: [String: Any] = ["file": path]
    let asset = AVURLAsset(url: URL(fileURLWithPath: path))
    do {
        let (duration, tracks) = try await asset.load(.duration, .tracks)
        result["duration"] = time(duration)
        var described: [[String: Any]] = []
        for track in tracks {
            let (size, transform, rate, range, formats, characteristics) = try await track.load(
                .naturalSize, .preferredTransform, .nominalFrameRate, .timeRange, .formatDescriptions,
                .mediaCharacteristics)
            described.append([
                "type": track.mediaType.rawValue,
                "size": [size.width, size.height],
                "transform": [transform.a, transform.b, transform.c, transform.d, transform.tx, transform.ty],
                "rate": rate,
                "range": time(range.start) + time(range.duration),
                "characteristics": characteristics.map { $0.rawValue }.sorted(),
                "formats": formats.map { format -> [String: Any] in
                    var extensions = CMFormatDescriptionGetExtensions(format) as? [String: Any] ?? [:]
                    for key in clearedExtensions { extensions.removeValue(forKey: key) }
                    return ["subtype": fourCC(CMFormatDescriptionGetMediaSubType(format)),
                            "extensions": describe(extensions)]
                },
            ])
        }
        result["tracks"] = described
        // Frames as QuickTime Player shows them: rotated and cropped, at exact times.
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        var frames: [[String: Any]] = []
        for fraction in [0.0, 0.5] {
            do {
                let (image, actual) = try await generator.image(at: CMTimeMultiplyByFloat64(duration, multiplier: fraction))
                frames.append(["size": [image.width, image.height], "time": time(actual),
                               "srgb": renderDigest(image) ?? NSNull()])
            } catch {
                frames.append(["error": error.localizedDescription])
            }
        }
        result["frames"] = frames
    } catch {
        result["error"] = error.localizedDescription
    }
    return result
}

for path in CommandLine.arguments.dropFirst() {
    let json = try! JSONSerialization.data(withJSONObject: await fingerprint(path), options: [.sortedKeys])
    print(String(data: json, encoding: .utf8)!)
}
