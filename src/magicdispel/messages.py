"""User-facing text in English and Simplified Chinese.

The language comes from MAGICDISPEL_LANG (en or zh) when set, then the usual
locale variables, then the macOS or Windows display language.
"""
import os
import plistlib
import sys
from pathlib import Path

TEXT = {
    "help": {
        "en": """\
magicdispel {version}: remove private metadata from photos, keeping image quality

Usage: type magicdispel and a space, drag photos into the terminal, press Enter.
       magicdispel photo.jpg screenshot.png ...

A cleaned copy is saved next to each original, named photo_clean.jpg, without
the dates and times the original's name may contain (as in screenshots).
Originals are never modified and existing files are never overwritten.

Options:
  --anonymous   name outputs photo_<random>.jpg instead of after the original
                (the picture itself is not changed)
  --keep-name   keep the original's name as it is, dates and times included
  --check       check that everything needed is installed
  --version     show the version
  -h, --help    show this help

Removes: location, capture time, camera and lens details, author, comments,
         editing software, and HEIC depth maps, portrait mattes, thumbnails.
Keeps:   image quality (no recompression), orientation, color, HDR,
         transparency and animation.
Formats: JPG, PNG, HEIC, AVIF, WebP, GIF, TIFF, BMP (BMP becomes lossless PNG).

This is not an anonymity tool: what the picture shows can still identify
people and places.""",
        "zh": """\
magicdispel {version}：清除照片里的隐私信息，画质不变

用法：输入 magicdispel 和一个空格，把照片拖进终端，按回车。
      magicdispel 照片.jpg 截图.png ...

清理后的新照片保存在原图旁边，名为 照片_clean.jpg。
原文件名里的日期和时间（比如截图名里的）会被去掉。
原图不会被修改，也不会覆盖任何已有文件（重名时自动编号）。

选项：
  --anonymous   新文件改用随机名字 photo_<随机字符>.jpg，不带原文件名
                （不会改动画面内容）
  --keep-name   原样保留原文件名，包括其中的日期和时间
  --check       检查运行所需的组件是否已安装
  --version     显示版本号
  -h, --help    显示这段帮助

会清除：定位、拍摄时间、相机与镜头信息、作者、注释、编辑软件，
        以及 HEIC 里的深度图、人像蒙版和缩略图。
会保留：画质（不重新压缩）、方向、色彩、HDR、透明度和动画。
支持：JPG、PNG、HEIC、AVIF、WebP、GIF、TIFF、BMP（BMP 会无损转成 PNG）。

注意：这不是匿名工具，照片画面本身仍可能暴露人物和地点。""",
    },
    "cleaned": {"en": "Cleaned: {path}", "zh": "已清理：{path}"},
    "failed": {"en": "Failed: {photo}\n  {reason}", "zh": "失败：{photo}\n  {reason}"},
    "summary": {"en": "Done: {succeeded} succeeded, {failed} failed.",
                "zh": "完成：{succeeded} 张成功，{failed} 张失败。"},
    "ready": {"en": "Ready: magicdispel {version}", "zh": "就绪：magicdispel {version}"},
    "second_check_on": {"en": "ExifTool second check: on (ExifTool {exiftool_version}, {exiftool})",
                        "zh": "ExifTool 复查：已开启（ExifTool {exiftool_version}，{exiftool}）"},
    "second_check_off": {"en": "ExifTool second check: off (ExifTool is optional and not installed)",
                         "zh": "ExifTool 复查：未开启（ExifTool 是可选的，未安装）"},
    "second_check_old": {"en": "ExifTool second check: off (ExifTool {exiftool_version} is older than 12.73)",
                         "zh": "ExifTool 复查：未开启（ExifTool {exiftool_version} 版本低于 12.73）"},
    "second_check_unusable": {"en": "ExifTool second check: off ({exiftool} does not run)",
                              "zh": "ExifTool 复查：未开启（{exiftool} 无法运行）"},
    "exiftool_misconfigured": {"en": "MAGICDISPEL_EXIFTOOL does not name a program: {path}",
                               "zh": "MAGICDISPEL_EXIFTOOL 指向的不是可执行程序：{path}"},
    "exiftool_problem": {"en": "ExifTool's second check found a problem ({detail}); nothing was saved.",
                         "zh": "ExifTool 复查发现问题（{detail}），没有保存。"},
    "error": {"en": "Error: {reason}", "zh": "错误：{reason}"},
    "bad_arguments": {"en": "Invalid arguments: {detail}\nRun magicdispel --help for usage.",
                      "zh": "参数有误：{detail}\n输入 magicdispel --help 查看用法。"},
    "cancelled": {"en": "\nCancelled.", "zh": "\n已取消。"},
    # Why a file was not cleaned. Nothing is saved in any of these cases.
    "not_a_file": {"en": "Not a readable file: {path}", "zh": "不是可读取的文件：{path}"},
    "unsupported_format": {"en": "This file type is not supported. Supported: JPG, PNG, HEIC, AVIF, "
                                 "WebP, GIF, TIFF and BMP.",
                           "zh": "不支持这种文件类型。支持：JPG、PNG、HEIC、AVIF、WebP、GIF、TIFF、BMP。"},
    "damaged": {"en": "This {format} file is damaged or incomplete.",
                "zh": "这个 {format} 文件已损坏或不完整。"},
    "unsupported_variant": {"en": "This kind of {format} file is not supported.",
                            "zh": "暂不支持这种 {format} 文件。"},
    "unsupported_part": {"en": "This {format} file contains data that cannot be handled safely ({part}).",
                         "zh": "这个 {format} 文件包含无法安全处理的数据（{part}）。"},
    "unsupported_profile": {"en": "The color profile in this {format} file cannot be cleaned safely ({detail}).",
                            "zh": "这个 {format} 文件的色彩配置无法安全清理（{detail}）。"},
    "undecodable": {"en": "This {format} file cannot be decoded to check the result, so it was not cleaned.",
                    "zh": "无法解码这个 {format} 文件来核对结果，因此没有清理。"},
    "raw_photo": {"en": "RAW photos (DNG, CR2, NEF, ARW and others) are not supported. Export a JPEG or HEIC "
                        "copy first.",
                  "zh": "暂不支持 RAW 照片（DNG、CR2、NEF、ARW 等），请先导出为 JPG 或 HEIC。"},
    "too_large": {"en": "This {format} image is too large to check safely (the limit is {limit} megapixels).",
                  "zh": "这张 {format} 图片太大，无法安全校验（上限为 {limit} 百万像素）。"},
    "extra_image_data": {"en": "The image data in this {format} file carries extra hidden bytes.",
                         "zh": "这个 {format} 文件的图像数据中夹带了多余的字节。"},
    "verification_failed": {"en": "The cleaned copy did not pass verification ({detail}); nothing was saved.",
                            "zh": "清理结果未通过校验（{detail}），没有保存。"},
    "pixels_changed": {"en": "The cleaned copy would not look identical to the original; nothing was saved.",
                       "zh": "清理后的画面与原图不一致，没有保存。"},
    "metadata_remains": {"en": "Metadata remained after cleaning ({tags}); nothing was saved.",
                         "zh": "清理后仍检测到元数据（{tags}），没有保存。"},
    "unexpected_error": {"en": "Unexpected error ({detail}); nothing was saved.",
                         "zh": "发生意外错误（{detail}），没有保存。"},
    "source_changed": {"en": "Another program changed the original while it was being cleaned; "
                             "nothing was saved. Please try again.",
                       "zh": "处理期间原图被其他程序改动，没有保存，请重试。"},
}


def message(key, **values):
    return TEXT[key][language()].format(**values)


def language():
    configured = os.environ.get("MAGICDISPEL_LANG", "").lower()
    if configured in {"en", "zh"}:
        return configured
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            return "zh" if value.lower().startswith("zh") else "en"
    return "zh" if system_language().lower().startswith("zh") else "en"


def system_language():
    """The display language when the terminal sets no locale, or ''."""
    try:
        if sys.platform == "darwin":
            preferences = Path.home() / "Library/Preferences/.GlobalPreferences.plist"
            with preferences.open("rb") as stream:
                return plistlib.load(stream).get("AppleLanguages", [""])[0]
        if sys.platform == "win32":
            import ctypes
            # Primary language 0x04 is Chinese; its sub-language picks the script.
            return "zh" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04 else ""
    except (OSError, ValueError, IndexError, AttributeError, plistlib.InvalidFileException):
        pass
    return ""
