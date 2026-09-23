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

A cleaned copy is saved next to each original, named photo_clean.jpg.
Originals are never modified and existing files are never overwritten.

Options:
  --anonymous   name outputs photo_<random>.jpg instead of after the original
                (the picture itself is not changed)
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
原图不会被修改，也不会覆盖任何已有文件（重名时自动编号）。

选项：
  --anonymous   新文件改用随机名字 photo_<随机字符>.jpg，不带原文件名
                （不会改动画面内容）
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
    "ready": {"en": "Ready: magicdispel {version} / ExifTool {exiftool_version}\nExifTool: {exiftool}",
              "zh": "就绪：magicdispel {version} / ExifTool {exiftool_version}\nExifTool：{exiftool}"},
    "error": {"en": "Error: {reason}", "zh": "错误：{reason}"},
    "bad_arguments": {"en": "Invalid arguments: {detail}\nRun magicdispel --help for usage.",
                      "zh": "参数有误：{detail}\n输入 magicdispel --help 查看用法。"},
    "cancelled": {"en": "\nCancelled.", "zh": "\n已取消。"},
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
