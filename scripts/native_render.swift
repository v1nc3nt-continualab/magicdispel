// Decode images with macOS ImageIO and render them through ColorSync, printing
// one JSON line of fingerprints per file. scripts/regression.py compiles this
// helper and compares fingerprints: equal hashes mean macOS shows the same image.
import CoreGraphics
import CryptoKit
import Foundation
import ImageIO

let maxFrames = 8
let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
let displayP3 = CGColorSpace(name: CGColorSpace.displayP3)!

// Auxiliary images MagicDispel either keeps (gain maps) or deliberately removes.
let gainMaps: [(String, CFString)] = [
    ("apple_gain_map", kCGImageAuxiliaryDataTypeHDRGainMap),
    ("iso_gain_map", kCGImageAuxiliaryDataTypeISOGainMap),
]
let removableAuxiliaries: [(String, CFString)] = [
    ("depth", kCGImageAuxiliaryDataTypeDepth),
    ("disparity", kCGImageAuxiliaryDataTypeDisparity),
    ("portrait_matte", kCGImageAuxiliaryDataTypePortraitEffectsMatte),
    ("skin_matte", kCGImageAuxiliaryDataTypeSemanticSegmentationSkinMatte),
    ("hair_matte", kCGImageAuxiliaryDataTypeSemanticSegmentationHairMatte),
    ("teeth_matte", kCGImageAuxiliaryDataTypeSemanticSegmentationTeethMatte),
    ("glasses_matte", kCGImageAuxiliaryDataTypeSemanticSegmentationGlassesMatte),
    ("sky_matte", kCGImageAuxiliaryDataTypeSemanticSegmentationSkyMatte),
]

func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// Hash rows of `length` bytes, skipping any padding at the end of each row.
func rowsDigest(_ base: UnsafeRawPointer, rows: Int, length: Int, stride: Int) -> String {
    var hasher = SHA256()
    for row in 0..<rows {
        hasher.update(bufferPointer: UnsafeRawBufferPointer(start: base + row * stride, count: length))
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
}

/// The decoder's own samples, before any color conversion.
func rawDigest(_ image: CGImage) -> String? {
    guard let data = image.dataProvider?.data as Data? else { return nil }
    let length = (image.width * image.bitsPerPixel + 7) / 8
    return data.withUnsafeBytes { buffer in
        rowsDigest(buffer.baseAddress!, rows: image.height, length: length, stride: image.bytesPerRow)
    }
}

/// What the image looks like after ColorSync converts its profile into `space`.
func renderDigest(_ image: CGImage, into space: CGColorSpace) -> String? {
    let info = CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder16Little.rawValue
    guard let context = CGContext(data: nil, width: image.width, height: image.height,
                                  bitsPerComponent: 16, bytesPerRow: 0, space: space,
                                  bitmapInfo: info),
          let base = context.data else { return nil }
    context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    return rowsDigest(base, rows: image.height, length: image.width * 8, stride: context.bytesPerRow)
}

func fingerprint(_ path: String) -> [String: Any] {
    var result: [String: Any] = ["file": path]
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else {
        result["error"] = "cannot open"
        return result
    }
    let count = CGImageSourceGetCount(source)
    result["count"] = count
    var frames: [[String: Any]] = []
    for index in 0..<min(count, maxFrames) {
        var frame: [String: Any] = [:]
        let properties = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [String: Any] ?? [:]
        frame["orientation"] = properties[kCGImagePropertyOrientation as String] ?? 1
        frame["dpi"] = [properties[kCGImagePropertyDPIWidth as String] ?? NSNull(),
                        properties[kCGImagePropertyDPIHeight as String] ?? NSNull()]
        let options = [kCGImageSourceShouldCacheImmediately: true] as CFDictionary
        if let image = CGImageSourceCreateImageAtIndex(source, index, options) {
            frame["size"] = [image.width, image.height]
            frame["raw"] = rawDigest(image) ?? NSNull()
            frame["srgb"] = renderDigest(image, into: srgb) ?? NSNull()
            frame["p3"] = renderDigest(image, into: displayP3) ?? NSNull()
        } else {
            frame["error"] = "cannot decode"
        }
        frames.append(frame)
    }
    result["frames"] = frames
    if count > 0 {
        let hdr = [kCGImageSourceDecodeRequest: kCGImageSourceDecodeToHDR,
                   kCGImageSourceShouldCacheImmediately: true] as CFDictionary
        if let image = CGImageSourceCreateImageAtIndex(source, 0, hdr) {
            result["hdr"] = rawDigest(image) ?? NSNull()
        }
        for (label, kind) in gainMaps {
            if let info = CGImageSourceCopyAuxiliaryDataInfoAtIndex(source, 0, kind) as? [String: Any],
               let bytes = info[kCGImageAuxiliaryDataInfoData as String] as? Data {
                result[label] = sha256(bytes)
            }
        }
        result["auxiliaries"] = removableAuxiliaries.compactMap { label, kind in
            CGImageSourceCopyAuxiliaryDataInfoAtIndex(source, 0, kind) == nil ? nil : label
        }
    }
    return result
}

for path in CommandLine.arguments.dropFirst() {
    autoreleasepool {
        let json = try! JSONSerialization.data(withJSONObject: fingerprint(path), options: [.sortedKeys])
        print(String(data: json, encoding: .utf8)!)
    }
}
